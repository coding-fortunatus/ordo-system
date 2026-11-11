"""Static feature catalog for the public marketing pages.

This used to be a hardcoded dict inside ``ems.views.feature_detail_view``.
Lifted into its own module so the API view (and the React frontend) can
share a single source of truth.
"""

FEATURES_DATA: dict[str, dict] = {
    "timetable-generation": {
        "title": "Timetable Generation",
        "subtitle": "An exam timetable with no clashes, built in minutes.",
        "overview": (
            "This is where every exam session starts. Give Ordo your courses, "
            "classes and exam dates, and it works out when each exam should "
            "happen. No class ends up with two exams at the same time, CBE and "
            "PBE exams are kept in their own slots, and exams are spread out so "
            "students aren't writing back to back."
        ),
        "icon": "calendar-check",
        "capabilities": [
            "Morning and afternoon sittings",
            "Separate slots for CBE exams",
            "Skips Sundays and any other days you choose",
            "Runs in the background while you watch its progress",
            "A view for each department and one for the whole school",
            "Download as CSV or as a full Excel broadsheet",
        ],
        "how_it_works": [
            "Upload your departments, courses, classes and halls.",
            "Pick the first and last day of the exam period.",
            "Press Generate and watch it work.",
            "Check the timetable and download it.",
        ],
        "benefits": [
            "Days of work with spreadsheets become a few minutes.",
            "No clashes, and no mistakes from copying things by hand.",
            "Staff and students get one clear timetable to follow.",
        ],
    },
    "hall-distribution": {
        "title": "Hall Distribution",
        "subtitle": "Every class sent to a hall that fits it.",
        "overview": (
            "Once the timetable is ready, Ordo decides which classes sit in "
            "which halls for every exam. It knows how many seats each hall has, "
            "so no hall is overfilled, and a class that's too big for one hall "
            "is split across several."
        ),
        "icon": "layout-grid",
        "capabilities": [
            "Matches classes to halls by size",
            "Splits big classes across more than one hall",
            "Shows how well each hall is being used",
            "Download the hall list as CSV",
            "Covers every date and sitting in one go",
        ],
        "how_it_works": [
            "Generate the timetable first.",
            "Press Generate on the Distribution page. It covers every exam at once.",
            "Pick a date and sitting to see which classes went where.",
            "Check the usage figures to see how full each hall is.",
        ],
        "benefits": [
            "Makes the most of the space you have.",
            "No overcrowded halls.",
            "You can see at a glance how well your halls are being used.",
        ],
    },
    "seat-allocation": {
        "title": "Seat Allocation",
        "subtitle": "A seat for every student, with no one next to a coursemate.",
        "overview": (
            "Ordo gives every student a numbered seat in their hall. Students "
            "taking the same course are never placed next to each other, not "
            "in front, behind, beside or on a diagonal, which makes copying far "
            "harder."
        ),
        "icon": "grid-3x3",
        "capabilities": [
            "Keeps coursemates apart in all eight directions",
            "Checkerboard or full-hall seating, depending on how much room you have",
            "A seat map for every hall",
            "Seat anyone who couldn't be placed by hand",
            "A summary of who has a seat and who doesn't",
        ],
        "how_it_works": [
            "Once halls are assigned, press Generate on the Allocation page.",
            "Ordo seats each hall, keeping coursemates apart.",
            "Open any hall to see its seat map.",
            "If anyone is left without a seat, give them one by hand.",
        ],
        "benefits": [
            "Much less chance of cheating.",
            "Invigilators get a clear seating plan for every hall.",
            "The seat map shows the whole hall at a glance.",
        ],
    },
    "reports-exports": {
        "title": "Reports & Exports",
        "subtitle": "Print-ready documents for every hall.",
        "overview": (
            "Everything you need on exam day, ready to print: attendance "
            "sheets, seating arrangements, the broadsheet and the timetable, "
            "all carrying your school's name and logo."
        ),
        "icon": "file-text",
        "capabilities": [
            "Word attendance sheets with your school's branding and space for signatures",
            "Excel broadsheet, by department or by day and sitting",
            "Timetable as CSV for each department",
            "Seating arrangements for each course, bundled in a ZIP",
            "Extra rows for students who register late",
        ],
        "how_it_works": [],
        "benefits": [
            "Documents are ready to print as soon as they're downloaded.",
            "Far less paperwork to prepare by hand.",
            "Every document matches, because they all come from the same data.",
        ],
    },
    "data-management": {
        "title": "Data Management",
        "subtitle": "Add, edit and bulk upload everything the exams depend on.",
        "overview": (
            "Admins and exam officers keep departments, courses, classes, halls "
            "and students up to date here, one record at a time or by the "
            "spreadsheet-full."
        ),
        "icon": "database",
        "capabilities": [
            "Add, edit and delete any record",
            "Bulk upload from CSV, with every row checked",
            "Upload departments, halls, courses, classes and students, one CSV at a time",
            "Spots duplicates before they cause problems",
            "Locks uploads once the timetable is made, so nothing shifts underneath it",
        ],
        "how_it_works": [],
        "benefits": [
            "Load a whole semester of data in minutes.",
            "Bad rows are caught before they get in.",
            "Clear error messages tell you exactly which row to fix.",
        ],
    },
    "background-jobs": {
        "title": "Background Job Monitor",
        "subtitle": "Keep an eye on long-running tasks, and retry any that fail.",
        "overview": (
            "Big tasks like building the timetable or seating every student run "
            "in the background, so the app stays quick while they work. The Jobs "
            "page shows you what's running, what finished and what went wrong."
        ),
        "icon": "list-checks",
        "capabilities": [
            "Live progress for every task",
            "Filter by status or type",
            "Retry a failed task with one click",
            "The full error when something goes wrong",
            "A history of every run, with its settings and results",
        ],
        "how_it_works": [],
        "benefits": [
            "The app stays responsive while heavy work runs.",
            "You always know how a task is getting on.",
            "Easy to recover when something fails.",
        ],
    },
}
