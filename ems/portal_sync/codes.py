"""Department and faculty codes for records sync creates (spec 0005, AC-18).

A code the portal sends (a mapped code column with a value) is cleaned and
checked; it is portal owned. Otherwise Ordo derives one from the name once,
at create, and never changes it again; it is Ordo owned.
"""
import re

MAX = 50
_USABLE = re.compile(r"^[A-Z0-9_-]{1,50}$")
_PREFIX = re.compile(
    r"^\s*(faculty|department|school|college|institute)\s+of\s+", re.IGNORECASE
)
_STOP_WORDS = {"of", "and", "the", "for", "in"}
_MAX_INITIALS = 10


def clean(value):
    """``(code, error)`` for a code the portal sent."""
    code = (value or "").strip().upper()
    if not _USABLE.match(code):
        return None, (
            f"Code '{value}' can't be used: letters, digits, - and _ only, "
            f"{MAX} at most."
        )
    return code, None


def derive(name, fallback, taken):
    """A free code from ``name`` (``Computer Science`` → ``CS``). ``taken``
    holds every code in use, upper cased; the result is added to it."""
    words = [
        w
        for w in re.split(r"[^A-Za-z0-9]+", _PREFIX.sub("", name or ""))
        if w and w.lower() not in _STOP_WORDS
    ]
    if len(words) > 1:
        base = "".join(w[0] for w in words[:_MAX_INITIALS])
    elif words:
        base = words[0][:3]
    else:
        base = fallback
    base = base.upper()
    code, n = base, 1
    while code in taken:
        n += 1
        suffix = str(n)
        code = base[: MAX - len(suffix)] + suffix
    taken.add(code)
    return code


def is_mapped(mapping, field):
    return bool((mapping or {}).get(field))
