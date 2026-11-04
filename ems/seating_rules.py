"""The per course seating rule, in one place (spec 0002).

Timetable, distribution, the allocator, reconcile and manual assignment all
read the limits and the neighbour sets from here, so the distribution model
and the seater cannot drift apart.

* ``strict``: no two students of a course touch in any of the 8 directions.
  One course is confined to one ``(r % 2, c % 2)`` quarter of a hall.
* ``relaxed``: only side, front and back neighbours are blocked. The course
  sits on the even parity seats (``r % 2 == c % 2``, quarters ``(0,0)`` and
  ``(1,1)``), which never share an edge. Used for a paper course too big for
  one period under the strict rule. At most one per period.

Everything here is pure: callers pass hall sizes as ``(rows, cols)`` pairs,
plus an optional seat ``mask`` for a hall that is not a full rectangle.

A mask is one string per row, ``X`` for a seat and ``.`` for no seat (short
row, aisle, pillar). Columns are physical positions, so a gap keeps the seats
either side of it from counting as neighbours. ``None`` means every cell of
the ``rows x cols`` grid is a seat. Seats are numbered 1..N over real seats
only, in the hall's seat order (:data:`SEAT_ORDERS`). The default, ``rows``,
numbers row by row from the left, so a full rectangle numbers exactly as
``r * cols + c + 1``. The allocator seats students in seat number order, so a
course's matric numbers follow whichever order the hall uses.
"""

from collections import Counter
from functools import lru_cache

STRICT = "strict"
RELAXED = "relaxed"
REFUSED = "refused"

RULE_CHOICES = [(STRICT, "Strict"), (RELAXED, "Relaxed")]

# Printed under the course details on every attendance sheet of a relaxed
# course, so invigilators watch the diagonal neighbours.
RELAXED_SHEET_LINE = "SEATING: RELAXED (DIAGONAL NEIGHBOURS ALLOWED)"

_EIGHT = [
    (-1, -1), (-1, 0), (-1, 1),
    (0, -1),           (0, 1),
    (1, -1),  (1, 0),  (1, 1),
]
_FOUR = [(-1, 0), (1, 0), (0, -1), (0, 1)]


def neighbours(rule: str) -> list:
    """Offsets a same course student may not occupy under ``rule``."""
    return list(_FOUR if rule == RELAXED else _EIGHT)


SEAT = "X"
GAP = "."

# How seats are numbered, as seen from the front of the hall (row 0 is the
# front row, column 0 the left). Snake orders turn at the end of each row
# (or column), so seat n and n + 1 are always side by side.
SEAT_ORDERS = {
    "rows": "Row by row, left to right",
    "rows_rtl": "Row by row, right to left",
    "snake": "Snake, first row left to right",
    "snake_rtl": "Snake, first row right to left",
    "columns": "Column by column, front to back",
    "columns_snake": "Snake down and up the columns",
}
DEFAULT_SEAT_ORDER = "rows"


def _sweep(rows: int, cols: int, seat_order: str):
    """Every cell of the grid in ``seat_order``."""
    if seat_order in ("columns", "columns_snake"):
        for c in range(cols):
            down = seat_order == "columns" or c % 2 == 0
            for r in range(rows) if down else reversed(range(rows)):
                yield r, c
        return
    for r in range(rows):
        if seat_order == "rows_rtl":
            left_first = False
        elif seat_order == "snake":
            left_first = r % 2 == 0
        elif seat_order == "snake_rtl":
            left_first = r % 2 == 1
        else:  # "rows", and any unknown order
            left_first = True
        for c in range(cols) if left_first else reversed(range(cols)):
            yield r, c
ALIGNMENTS = ("left", "center", "right")

# A hall's grid is materialised cell by cell (HallLayout) and cached per
# process, so its size must be bounded: one oversized hall would otherwise
# exhaust memory in the shared web and worker processes.
MAX_ROWS = 200
MAX_COLUMNS = 200
MAX_CELLS = 20_000


def check_grid(rows, cols) -> None:
    """``ValueError`` (fit for the user) when ``rows x cols`` is too big."""
    rows, cols = int(rows or 0), int(cols or 0)
    if rows > MAX_ROWS:
        raise ValueError(f"A hall can have at most {MAX_ROWS} rows.")
    if cols > MAX_COLUMNS:
        raise ValueError(f"A hall can have at most {MAX_COLUMNS} columns.")
    if rows * cols > MAX_CELLS:
        raise ValueError(f"A hall can have at most {MAX_CELLS:,} cells (rows x columns).")


def normalise_mask(rows: int, cols: int, mask):
    """``mask`` checked against ``rows x cols`` and returned as a tuple, or
    ``None`` when it is empty or every cell is a seat (a full rectangle keeps
    the fast path). Raises ``ValueError`` with a message fit for the user."""
    check_grid(rows, cols)
    if not mask:
        return None
    if len(mask) > MAX_ROWS:
        raise ValueError(f"A hall can have at most {MAX_ROWS} rows.")
    mask = tuple(str(line).upper() for line in mask)
    if len(mask) != rows:
        raise ValueError(f"Layout has {len(mask)} rows but the hall has {rows}.")
    for number, line in enumerate(mask, 1):
        if len(line) != cols:
            raise ValueError(
                f"Layout row {number} is {len(line)} wide but the hall has {cols} columns."
            )
        if set(line) - {SEAT, GAP}:
            raise ValueError(
                f"Layout row {number} may only use '{SEAT}' (seat) and '{GAP}' (no seat)."
            )
    if all(line == SEAT * cols for line in mask):
        return None
    if not any(SEAT in line for line in mask):
        raise ValueError("Layout has no seats.")
    return mask


def mask_from_row_seats(row_seats, align: str = "left"):
    """``(rows, cols, mask)`` for a hall given as seats per row, front row
    first. ``align`` places each short row against the ``left`` wall, the
    ``right`` wall, or in the ``center``."""
    row_seats = list(row_seats)
    if len(row_seats) > MAX_ROWS:
        raise ValueError(f"A hall can have at most {MAX_ROWS} rows.")
    row_seats = [int(n) for n in row_seats]
    if row_seats and max(row_seats) > MAX_COLUMNS:
        raise ValueError(f"A hall can have at most {MAX_COLUMNS} columns.")
    if not row_seats or any(n < 0 for n in row_seats) or max(row_seats) <= 0:
        raise ValueError("Seats per row must be whole numbers with at least one seat.")
    align = (align or "left").strip().lower()
    if align not in ALIGNMENTS:
        raise ValueError(f"Alignment must be one of {', '.join(ALIGNMENTS)}.")
    cols = max(row_seats)
    mask = []
    for n in row_seats:
        pad = cols - n
        left = {"left": 0, "center": pad // 2, "right": pad}[align]
        mask.append(GAP * left + SEAT * n + GAP * (pad - left))
    return len(row_seats), cols, normalise_mask(len(row_seats), cols, mask)


def row_seats(rows: int, cols: int, mask=None) -> list:
    """Seats in each row, front row first."""
    if mask is None:
        return [max(cols, 0)] * min(max(rows, 0), MAX_ROWS)
    return [line.count(SEAT) for line in mask]


class HallLayout:
    """The real seats of one hall. Build it with :func:`hall_layout` (cached)
    or :func:`layout_of`, never directly in a loop."""

    __slots__ = ("rows", "cols", "mask", "seat_order", "seats", "_numbers", "quarters")

    def __init__(self, rows: int, cols: int, mask=None, seat_order=DEFAULT_SEAT_ORDER):
        self.rows = max(rows or 0, 0)
        self.cols = max(cols or 0, 0)
        # Rows stored before the limits existed must not materialise either.
        check_grid(self.rows, self.cols)
        self.mask = mask
        self.seat_order = seat_order
        # In seat order, so position i holds seat number i + 1.
        self.seats = tuple(
            (r, c)
            for r, c in _sweep(self.rows, self.cols, seat_order)
            if mask is None or mask[r][c] == SEAT
        )
        self._numbers = {cell: i + 1 for i, cell in enumerate(self.seats)}
        counts = Counter((r % 2, c % 2) for r, c in self.seats)
        self.quarters = {(ro, co): counts[(ro, co)] for ro in (0, 1) for co in (0, 1)}

    @property
    def seat_count(self) -> int:
        return len(self.seats)

    def is_seat(self, row: int, col: int) -> bool:
        return (row, col) in self._numbers

    def seat_number(self, row: int, col: int) -> int:
        return self._numbers[(row, col)]

    def cell(self, seat_number: int):
        """``(row, col)`` of a 1-based seat number, or ``None`` if the hall
        has no such seat."""
        if 1 <= seat_number <= len(self.seats):
            return self.seats[seat_number - 1]
        return None


@lru_cache(maxsize=1024)
def hall_layout(
    rows: int, cols: int, mask=None, seat_order=DEFAULT_SEAT_ORDER
) -> HallLayout:
    """Cached :class:`HallLayout`. ``mask`` must be a tuple (or ``None``)."""
    return HallLayout(rows, cols, mask, seat_order or DEFAULT_SEAT_ORDER)


def mask_key(mask):
    """A stored mask (JSON list) as a hashable cache key."""
    return tuple(mask) if mask else None


def layout_of(hall) -> HallLayout:
    """The layout of a ``Hall`` model instance."""
    return hall_layout(
        hall.rows or 0, hall.columns or 0, mask_key(hall.layout), hall.seat_order
    )


def hall_per_course_slice(
    rows: int, cols: int, pattern: str = "checkerboard", mask=None
) -> int:
    """Largest course that's guaranteed to fit any quarter of the hall.

    Each course is confined to one ``(r%2, c%2)`` parity quarter so all its
    students stay pairwise non-8-dir-adjacent. When ``rows`` or ``cols`` is
    odd the four quarters have unequal sizes (e.g. a 15×20 hall has two
    80-cell and two 70-cell quarters). To guarantee zero adjacency-overflow
    we cap at the *smallest* quarter — ``floor(r/2) × floor(c/2)`` — rather
    than the largest. Pattern is irrelevant; same-course adjacency binds.
    With a ``mask`` the quarters are counted from the real seats.
    """
    del pattern
    if rows <= 0 or cols <= 0:
        return 0
    if mask is None:
        return max(1, rows // 2) * max(1, cols // 2)
    return min(hall_layout(rows, cols, mask_key(mask)).quarters.values())


def largest_quarter(rows: int, cols: int, mask=None) -> int:
    """Seats in the biggest ``(r%2, c%2)`` quarter (quarter ``(0,0)`` for a
    full rectangle).

    Distribution caps a strict course per hall at this, not at
    :func:`hall_per_course_slice`: a course may fill a bigger quarter
    whole. ``_quarters_can_seat`` still verifies every bite, so a course is
    only given the big quarter while one is free. ``strict_limit`` stays on
    the smallest quarter so timetable classification remains a guarantee."""
    if rows <= 0 or cols <= 0:
        return 0
    return max(hall_layout(rows, cols, mask_key(mask)).quarters.values())


def even_half(rows: int, cols: int, mask=None) -> int:
    """Even parity seats of the hall: quarters ``(0,0)`` + ``(1,1)``. The
    most one relaxed course can take in it, and the checkerboard seats."""
    if rows <= 0 or cols <= 0:
        return 0
    quarters = hall_layout(rows, cols, mask_key(mask)).quarters
    return quarters[(0, 0)] + quarters[(1, 1)]


def _unpack(hall):
    """``(rows, cols, mask)`` from a ``(rows, cols)`` or
    ``(rows, cols, mask)`` tuple."""
    rows, cols, *rest = hall
    return rows, cols, (rest[0] if rest else None)


def strict_limit(halls) -> int:
    """Students one strict course can be seated for across ``halls`` in one
    period. ``halls`` holds ``(rows, cols)`` or ``(rows, cols, mask)``
    tuples. A hall with fewer than 2 rows or 2 columns counts as 0."""
    total = 0
    for rows, cols, mask in map(_unpack, halls):
        if rows >= 2 and cols >= 2:
            total += hall_per_course_slice(rows, cols, mask=mask)
    return total


def relaxed_limit(halls) -> int:
    """Students one relaxed course can be seated for across ``halls``."""
    return sum(
        even_half(rows, cols, mask) for rows, cols, mask in map(_unpack, halls)
    )


def classify(students: int, strict_limit: int, relaxed_limit: int) -> str:
    """The rule a paper course of ``students`` gets. CBE is never classified."""
    if students <= strict_limit:
        return STRICT
    if students <= relaxed_limit:
        return RELAXED
    return REFUSED


def even_seats(rows: int, cols: int, mask=None, seat_order=DEFAULT_SEAT_ORDER) -> list:
    """Even parity seats in seat number order. The allocator seats a
    relaxed course along exactly this list."""
    return [
        (r, c)
        for r, c in hall_layout(rows, cols, mask_key(mask), seat_order).seats
        if r % 2 == c % 2
    ]


def relaxed_quarter_usage(
    rows: int, cols: int, k: int, mask=None, seat_order=DEFAULT_SEAT_ORDER
) -> tuple:
    """``(in_q00, in_q11)``: how many of the first ``k`` seats of
    :func:`even_seats` fall in quarter ``(0,0)`` and quarter ``(1,1)``."""
    return _relaxed_quarter_usage(rows, cols, k, mask_key(mask), seat_order)


@lru_cache(maxsize=4096)
def _relaxed_quarter_usage(rows: int, cols: int, k: int, mask, seat_order) -> tuple:
    # Cached: the distribution model asks inside a binary search per hall.
    in_q00 = sum(
        1 for r, _c in even_seats(rows, cols, mask, seat_order)[: max(k, 0)] if r % 2 == 0
    )
    return in_q00, min(max(k, 0), even_half(rows, cols, mask)) - in_q00
