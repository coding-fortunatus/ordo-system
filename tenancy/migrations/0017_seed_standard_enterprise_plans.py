from django.db import migrations

# The plans in the pricing proposal (v1.1). The student limits are the
# proposal's unapproved placeholders: edit them in the console once decided.
# The existing Starter plan is left as it is, so no school on it loses a
# feature; edit it in the console after moving its schools.
PLANS = [
    {
        "code": "standard-10k",
        "name": "Standard (up to 10,000 students)",
        "max_students": 10_000,
        "features": ["distribution", "exports", "publication", "seat_allocation"],
    },
    {
        "code": "standard-25k",
        "name": "Standard (up to 25,000 students)",
        "max_students": 25_000,
        "features": ["distribution", "exports", "publication", "seat_allocation"],
    },
    {
        "code": "enterprise",
        "name": "Enterprise",
        "max_students": None,
        "features": [
            "branding", "distribution", "exports", "portal_sync", "publication", "seat_allocation",
        ],
    },
]


def seed(apps, schema_editor):
    """Add the plans that aren't there yet. A plan whose code already exists
    is left alone, so a console edit is never overwritten."""
    Plan = apps.get_model("tenancy", "Plan")
    for plan in PLANS:
        Plan.objects.get_or_create(
            code=plan["code"],
            defaults={
                "name": plan["name"],
                "max_students": plan["max_students"],
                "max_staff_users": None,
                "features": plan["features"],
                "is_active": True,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0016_distribution_feature"),
    ]

    # Plans are never deleted, so the reverse leaves them in place.
    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
