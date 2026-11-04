"""Ordo support sessions: a platform admin inside a school, for an hour at most.

The console issues a one time ticket (60 s, bound to one school, stored
hashed). The school host redeems it for a token of a hidden Super Admin,
``support+<admin_id>@ordo.invalid``, whose ``support_expires_at`` cuts it
off on time whatever the client does (spec 0003, child 0006).
"""
import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django_tenants.utils import schema_context
from rest_framework import status
from rest_framework.authtoken.models import Token

from ems.api.exceptions import CodedAPIException, Conflict
from tenancy import audit
from tenancy.models import School, SupportSession

TICKET_TTL = timedelta(seconds=60)
SUPPORT_SESSION_TTL = timedelta(hours=1)

# A school an admin may start a session in. Redeeming is let through the
# status gate for the same two.
STARTABLE_STATUSES = (School.Status.ACTIVE, School.Status.SUSPENDED)


class TicketGone(CodedAPIException):
    status_code = status.HTTP_410_GONE
    default_detail = "This support link has expired."
    default_code = "support_ticket_gone"


def hash_ticket(ticket):
    return hashlib.sha256(ticket.encode()).hexdigest()


def shadow_email(admin_id):
    # .invalid is reserved (RFC 2606): it can never be someone's address.
    return f"support+{admin_id}@ordo.invalid"


def open_sessions():
    """Sessions not yet ended, whether or not the ticket was used."""
    return SupportSession.objects.filter(ended_at__isnull=True)


def start(request, school, reason):
    """Issue a ticket for ``school``. Returns the session and the link to open.

    Call inside a transaction holding the school row's lock.
    """
    if school.status not in STARTABLE_STATUSES:
        raise Conflict(
            "Support sessions need an active or suspended school. This one is "
            f"{school.get_status_display().lower()}."
        )
    ticket = secrets.token_urlsafe(32)
    session = SupportSession.objects.create(
        admin=request.user,
        school=school,
        reason=reason,
        ticket_hash=hash_ticket(ticket),
        ticket_expires_at=timezone.now() + TICKET_TTL,
    )
    audit.record(request, "support_ticket_issued", school, session_id=session.pk)
    domain = school.get_primary_domain().domain
    return session, f"{settings.PLATFORM_URL_SCHEME}://{domain}/support?ticket={ticket}"


@transaction.atomic
def redeem(request, ticket):
    """Start the session the ticket names, in this school. Returns it and a token.

    Runs on the school host, so the connection is already in its schema.
    """
    from ems.models import AuditLog, User

    now = timezone.now()
    session = (
        SupportSession.objects.select_for_update(of=("self",))
        .select_related("admin")
        .filter(
            ticket_hash=hash_ticket(ticket or ""),
            school=request.tenant,
            ticket_used_at__isnull=True,
            ticket_expires_at__gt=now,
            ended_at__isnull=True,
            admin__is_active=True,
        )
        .first()
    )
    if session is None:
        raise TicketGone()
    admin = session.admin

    # One shadow user per admin per school: a session still open for the
    # same pair would share its token, so this one replaces it.
    for earlier in open_sessions().select_for_update().filter(
        admin=admin, school=session.school, started_at__isnull=False
    ).exclude(pk=session.pk):
        _close(request, earlier, now, reason="replaced")

    session.ticket_used_at = session.started_at = now
    session.expires_at = now + SUPPORT_SESSION_TTL

    user, created = User.all_objects.get_or_create(
        email=shadow_email(admin.pk),
        defaults={"support_expires_at": session.expires_at},
    )
    user.role = User.Role.SUPER_ADMIN
    user.first_name = "Ordo support"
    user.last_name = admin.name[:30]
    user.department = None
    user.is_active = True
    user.support_expires_at = session.expires_at
    if created:
        user.set_unusable_password()
    user.save()
    # The post_save signal made one on creation; it's one per user either way.
    Token.objects.filter(user=user).delete()
    token = Token.objects.create(user=user)

    session.tenant_user_id = user.pk
    session.save(
        update_fields=["ticket_used_at", "started_at", "expires_at", "tenant_user_id"]
    )
    AuditLog.objects.create(
        user=user,
        user_email=user.email,
        action=f"Ordo support session started: {session.reason}"[:255],
        method=request.method,
        path=request.path[:512],
        object_type="support",
        status_code=200,
        ip_address=audit.client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:512],
        metadata={"admin_name": admin.name, "expires_at": session.expires_at.isoformat()},
    )
    audit.record(
        request, "support_started", session.school, admin=admin,
        session_id=session.pk, reason=session.reason,
        expires_at=session.expires_at.isoformat(),
    )
    return session, token


def end_for_admin(request, admin, reason):
    """End every open support session of ``admin``; returns how many.
    Any revocation of a platform admin (deactivation, sign-out, 2FA reset)
    must call this, or their school support token outlives it.

    Locks the session rows; call inside a transaction."""
    ended = 0
    for session in open_sessions().filter(admin=admin):
        end(request, session, reason=reason)
        ended += 1
    return ended


def end(request, session, reason="ended"):
    """End ``session`` now and cut off its token. 409 if it already ended.

    Locks the session row; call inside a transaction.
    """
    session = (
        SupportSession.objects.select_for_update(of=("self",))
        .select_related("school", "admin")
        .get(pk=session.pk)
    )
    if session.ended_at is not None:
        raise Conflict("This support session has already ended.")
    _close(request, session, timezone.now(), reason=reason)
    return session


def _close(request, session, now, *, reason):
    from ems.models import User

    session.ended_at = now
    session.save(update_fields=["ended_at"])
    if session.tenant_user_id and session.school.status in School.LIVE_STATUSES:
        with schema_context(session.school.schema_name):
            # Only shorten: a later session for the same admin may own the user now.
            User.all_objects.filter(
                pk=session.tenant_user_id, support_expires_at__gt=now
            ).update(support_expires_at=now)
            Token.objects.filter(user_id=session.tenant_user_id).delete()
    audit.record(
        request, "support_ended", session.school, admin=session.admin,
        session_id=session.pk, reason=reason,
    )


def current_session(user, school):
    """The open session behind a support user's token, or None."""
    if user.support_expires_at is None:
        return None
    return (
        open_sessions()
        .select_related("admin")
        .filter(school=school, tenant_user_id=user.pk, started_at__isnull=False)
        .order_by("-started_at")
        .first()
    )
