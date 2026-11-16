"""Feature keys a plan can switch on (spec 0003, child 0005)."""

KEYS = frozenset(
    {"distribution", "seat_allocation", "exports", "publication", "branding", "portal_sync"}
)

# A feature that only works on top of another: seats are planned from the
# hall distribution, so seat allocation without distribution is refused.
REQUIRES = {"seat_allocation": "distribution"}


def missing_requirement(keys):
    """A message naming the first feature in ``keys`` whose prerequisite is
    missing, or None when every prerequisite is on."""
    present = set(keys or ())
    for key, needed in sorted(REQUIRES.items()):
        if key in present and needed not in present:
            return f"{_LABELS[key]} needs {_LABELS[needed]} switched on too."
    return None


_LABELS = {
    "distribution": "Distribution",
    "seat_allocation": "Seat allocation",
}
