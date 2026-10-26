"""CampusCare - Smart Campus Complaint & Maintenance System."""

import csv
import io
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import Store, esc, form, heading, note, run, table


store = Store(
    Path(__file__).with_name("data.json"),
    {"tickets": [], "settings": {"sla_hours": 72}}
)

CATEGORIES = [
    "Electrical", "Water", "Network", "Cleaning",
    "Furniture", "Safety", "Other"
]

STATUSES = [
    "Pending", "Assigned", "In Progress", "Resolved", "Reopened"
]

PRIORITIES = ["Low", "Medium", "High", "Urgent"]


def now():
    return datetime.now().replace(microsecond=0)


def stamp():
    return now().isoformat(sep=" ")


def normalize(ticket):
    """Supply defaults for older tickets without deleting their data."""
    ticket.setdefault("category", "Other")
    ticket.setdefault("location", "Not specified")
    ticket.setdefault("description", "")
    ticket.setdefault("status", "Pending")
    ticket.setdefault("priority", "Medium")
    ticket.setdefault("assigned_to", "")
    ticket.setdefault("created", date.today().isoformat())
    ticket.setdefault("created_at", ticket.get("created", stamp()))
    ticket.setdefault("history", [])
    ticket.setdefault("comments", [])
    ticket.setdefault("reporter", "Campus user")
    ticket.setdefault("rating", None)
    ticket.setdefault("feedback", "")
    ticket.setdefault(
        "due_at",
        (
            now() + timedelta(
                hours=int(
                    store.data.get("settings", {}).get("sla_hours", 72)
                )
            )
        ).isoformat(sep=" ")
    )

    if not isinstance(ticket["history"], list):
        ticket["history"] = []

    if not isinstance(ticket["comments"], list):
        ticket["comments"] = []

    return ticket


def all_tickets():
    return [
        normalize(ticket)
        for ticket in store.data["tickets"]
        if isinstance(ticket, dict)
    ]


def ticket_id(ticket):
    return f"CC-{int(ticket.get('id', 0)):05d}"


def find_ticket(value):
    raw = str(value or "").strip().upper().replace("CC-", "")

    if not raw.isdigit():
        raise ValueError(
            "Enter a valid ticket number, for example CC-00104."
        )

    wanted = int(raw)

    for ticket in all_tickets():
        if int(ticket.get("id", -1)) == wanted:
            return ticket

    raise ValueError(f"Ticket CC-{wanted:05d} was not found.")


def parse_dt(value):
    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        ).replace(tzinfo=None)
    except (ValueError, TypeError):
        try:
            return datetime.combine(
                date.fromisoformat(str(value)[:10]),
                datetime.min.time()
            )
        except (ValueError, TypeError):
            return now()


def is_overdue(ticket):
    return (
        ticket["status"] != "Resolved"
        and parse_dt(ticket.get("due_at")) < now()
    )


def add_history(ticket, event, details=""):
    ticket.setdefault("history", []).append({
        "time": stamp(),
        "event": event,
        "details": details
    })


def similarity(text_a, text_b):
    words_a = set(re.findall(r"\w+", text_a.lower()))
    words_b = set(re.findall(r"\w+", text_b.lower()))

    union = words_a | words_b

    return len(words_a & words_b) / len(union) if union else 0


def possible_duplicates(candidate):
    found = []

    for ticket in all_tickets():
        if ticket["status"] == "Resolved":
            continue

        same_location = (
            ticket.get("location", "").strip().lower()
            == candidate["location"].strip().lower()
        )

        same_category = (
            ticket.get("category", "").lower()
            == candidate["category"].lower()
        )

        score = similarity(
            candidate["description"],
            ticket["description"]
        )

        if same_location and same_category and score >= 0.30:
            found.append((ticket, score))

    return sorted(
        found, key=lambda item: item[1], reverse=True
    )[:5]


def priority_score(ticket):
    points = {
        "Urgent": 100,
        "High": 65,
        "Medium": 35,
        "Low": 10
    }.get(ticket.get("priority"), 35)

    if is_overdue(ticket):
        points += 25

    age_days = (
        now() - parse_dt(
            ticket.get("created_at", ticket.get("created"))
        )
    ).days

    points += min(20, max(0, age_days * 2))

    if ticket.get("category") == "Safety":
        points += 20

    return points


def pill(value, extra=""):
    css_class = str(value).lower().replace(" ", "-")
    return (
        f'<span class="tag {esc(css_class)}">'
        f'{esc(value)}</span>'
    )


def stat_card(label, number, detail, icon, glow, anchor):
    return (
        f'<a href="#{anchor}" class="kpi" style="--glow:{glow}">'
        f'<div class="kpi-head">{esc(label)}'
        f'<span class="kpi-icon">{icon}</span></div>'
        f'<div class="kpi-number">{number}</div>'
        f'<div class="kpi-note">{esc(detail)}</div></a>'
    )


def ticket_rows(tickets):
    rows = []

    for ticket in tickets:
        rows.append([
            ticket_id(ticket),
            ticket.get("category", "Other"),
            ticket.get("location", ""),
            ticket.get("description", "")[:95],
            ticket.get("priority", "Medium"),
            ticket.get("status", "Pending"),
            ticket.get("assigned_to", "") or "Unassigned",
            (
                "OVERDUE" if is_overdue(ticket)
                else ticket.get("due_at", "")[:16].replace("T", " ")
            )
        ])

    return rows


def nav():
    items = [
        ("overview", "▦", "Command center"),
        ("register", "＋", "New complaint"),
        ("priority-board", "⚑", "Priority queue"),
        ("search", "⌕", "Search & filter"),
        ("manage", "↻", "Update ticket"),
        ("technicians", "♙", "Technician hub"),
        ("history", "◷", "Ticket timeline"),
        ("feedback", "☆", "Resolution feedback"),
        ("all-complaints", "▤", "All complaints"),
        ("analytics", "▥", "Insights & reports"),
        ("settings", "⚙", "SLA settings"),
    ]

    out = (
        '<aside class="sidebar">'
        '<a class="brand" href="#overview">'
        '<span class="brand-mark">✦</span>'
        '<span><strong>CampusCare</strong>'
        '<small>SMART HELP DESK</small></span></a>'
        '<div class="nav-label">WORKSPACE</div>'
        '<nav class="nav">'
    )

    out += "".join(
        f'<a href="#{key}"><span class="ico">{icon}</span>'
        f'{esc(label)}</a>'
        for key, icon, label in items
    )

    out += (
        '</nav><div class="sidebar-foot">'
        '<strong>Campus operations</strong><br>'
        'Track issues. Assign work. Improve campus life.'
        '</div></aside>'
    )

    return out


def dashboard():
    tickets = all_tickets()
    total = len(tickets)

    pending = sum(
        t["status"] in ("Pending", "Reopened") for t in tickets
    )

    active = sum(
        t["status"] in ("Assigned", "In Progress") for t in tickets
    )

    resolved = sum(
        t["status"] == "Resolved" for t in tickets
    )

    overdue = sum(is_overdue(t) for t in tickets)

    rate = round(resolved / total * 100) if total else 0

    out = (
        '<section class="welcome">'
        '<div class="eyebrow">CAMPUS OPERATIONS / OVERVIEW</div>'
        '<h1 class="page-title">Good day, Help Desk 👋</h1>'
        '<p class="subtitle">Here’s what’s happening across campus. '
        'Prioritize issues, coordinate technicians, and keep every '
        'ticket moving.</p></section>'
    )

    out += '<div class="kpis dashboard-only">'

    out += stat_card(
        "Total tickets", total, "All recorded complaints",
        "▤", "#e8eeff", "all-complaints"
    )

    out += stat_card(
        "Needs attention", pending, "Pending or reopened",
        "◷", "#fff0d6", "pending-list"
    )

    out += stat_card(
        "In progress", active, "Assigned or being handled",
        "↗", "#e2edff", "progress-list"
    )

    out += stat_card(
        "Resolved", resolved, f"{rate}% overall resolution rate",
        "✓", "#dcf8e8", "resolved-list"
    )

    # Close KPI grid before rendering the status lists.
    out += '</div>'

    # Dedicated status lists targeted by the dashboard KPI cards.
    status_sections = [
        (
            "pending-list",
            "Pending / Reopened Complaints",
            ("Pending", "Reopened"),
            "No pending or reopened complaints."
        ),
        (
            "progress-list",
            "In-progress Complaints",
            ("Assigned", "In Progress"),
            "No complaints are currently in progress."
        ),
        (
            "resolved-list",
            "Resolved Complaints",
            ("Resolved",),
            "No resolved complaints yet."
        )
    ]

    for section_id, title, statuses, empty_message in status_sections:
        matching = [
            ticket for ticket in tickets
            if ticket.get("status", "Pending") in statuses
        ]
        matching.sort(
            key=lambda ticket: int(ticket.get("id", 0)),
            reverse=True
        )

        out += (
            f'<section class="card tool-panel status-section" id="{section_id}">'
            f'<div class="card-head"><div>'
            f'<h2>{esc(title)}</h2>'
            f'<p class="card-desc">{len(matching)} matching complaint(s)</p>'
            f'</div></div>'
        )

        if matching:
            out += table(
                [
                    "Ticket", "Category", "Location", "Description",
                    "Priority", "Status", "Technician", "Deadline"
                ],
                ticket_rows(matching)
            )
        else:
            out += f'<div class="notice">{esc(empty_message)}</div>'

        out += '</section>'

    out += (
        '<div class="grid-main dashboard-only">'
        '<section class="card"><div class="card-head">'
        '<div><h2>Complaint distribution</h2>'
        '<p class="card-desc">Current workload by status</p></div>'
        '<a class="link" href="#analytics">View insights →</a>'
        '</div>'
    )

    distribution = [
        ("Pending / reopened", pending, "#f4ad38"),
        ("Assigned / in progress", active, "#4c78ff"),
        ("Resolved", resolved, "#23b779")
    ]

    for label, count, color in distribution:
        percentage = round(count / total * 100) if total else 0

        out += (
            f'<div class="bar-row"><span>{esc(label)}</span>'
            f'<div class="bar-track"><div class="bar-fill" '
            f'style="width:{percentage}%;background:{color}">'
            f'</div></div><strong>{count}</strong></div>'
        )

    out += (
        f'<div class="notice">Resolution rate: <strong>{rate}%</strong>'
        f' · Overdue open tickets: <strong>{overdue}</strong>. '
        f'{"Review overdue work first." if overdue else "No tickets currently exceed their target deadline."}'
        '</div></section>'
    )

    out += (
        '<section class="card"><div class="card-head">'
        '<div><h2>Needs attention</h2>'
        '<p class="card-desc">Highest-priority unresolved tickets</p></div>'
        '<a class="link" href="#priority-board">Open queue →</a>'
        '</div><div class="priority-list">'
    )

    priority = sorted(
        [t for t in tickets if t["status"] != "Resolved"],
        key=priority_score,
        reverse=True
    )[:4]

    if priority:
        for ticket in priority:
            label = (
                "Overdue" if is_overdue(ticket)
                else ticket.get("priority", "Medium")
            )

            out += (
                '<div class="priority-item">'
                '<span class="priority-icon">⚑</span><div>'
                f'<strong>{esc(ticket_id(ticket))} · '
                f'{esc(ticket["location"])}</strong>'
                f'<small>{esc(ticket["category"])} · '
                f'{esc(ticket["description"][:55])}</small></div>'
                f'{pill(label)}</div>'
            )
    else:
        out += (
            '<div class="notice">'
            'All clear — there are no open complaints.</div>'
        )

    out += '</div></section></div>'

    recent = sorted(
        tickets,
        key=lambda t: t.get("created_at", t.get("created", "")),
        reverse=True
    )[:6]

    out += (
        '<section class="card dashboard-only">'
        '<div class="card-head"><div><h2>Recent tickets</h2>'
        '<p class="card-desc">Latest activity in your help desk</p></div>'
        '<a class="link" href="#all-complaints">View all →</a>'
        '</div>'
    )

    out += table(
        [
            "Ticket", "Category", "Location", "Priority",
            "Status", "Technician", "Deadline"
        ],
        [
            [
                ticket_id(t), t["category"], t["location"],
                t["priority"], t["status"],
                t["assigned_to"] or "Unassigned",
                (
                    "OVERDUE" if is_overdue(t)
                    else t["due_at"][:16].replace("T", " ")
                )
            ]
            for t in recent
        ]
    )

    out += '</section>'

    return out


def view():
    tickets = all_tickets()

    out = (
        '<div class="layout">' + nav()
        + '<main class="main"><header class="topbar"><div>'
        '<div class="eyebrow">SMART CAMPUS MAINTENANCE</div>'
        '<h1 class="page-title">CampusCare</h1>'
        '<p class="subtitle">A clearer, faster way to keep campus '
        'facilities running.</p></div><div class="top-actions">'
        '<span class="pill">● Local data saved</span>'
        '<button class="btn secondary" type="button" '
        'onclick="toggleTheme()">◐ Theme</button>'
        '<a class="btn primary" href="#register">＋ New ticket</a>'
        '</div></header>'
    )

    out += dashboard()

    # Register a complaint.
    out += (
        '<section class="card tool-panel" id="register">'
        '<div class="card-head"><div><h2>✦ Create a complaint</h2>'
        '<p class="card-desc">Share a clear description so the right '
        'team can act quickly.</p></div>'
        '<span class="tag in-progress">New ticket</span></div>'
    )

    out += form(
        "add",
        [
            ("category", "Issue category", "select", CATEGORIES),
            ("location", "Campus location / block", "text", ""),
            ("priority", "Priority level", "select", PRIORITIES),
            ("reporter", "Reporter name (optional)", "text_optional", ""),
            (
                "description", "What happened? Include useful details",
                "textarea", ""
            )
        ],
        "Create ticket →"
    )

    out += (
        '<div class="notice">Smart check: before saving, CampusCare '
        'checks for similar unresolved tickets at the same location '
        'and in the same category.</div></section>'
    )

    # Priority queue.
    out += (
        '<section class="card tool-panel" id="priority-board">'
        '<div class="card-head"><div><h2>⚑ Priority queue</h2>'
        '<p class="card-desc">Urgent, overdue, and older tickets are '
        'ranked first.</p></div>'
        '<a class="btn secondary" href="/export.csv">⇩ Export CSV</a>'
        '</div>'
    )

    queue = sorted(
        [t for t in tickets if t["status"] != "Resolved"],
        key=priority_score,
        reverse=True
    )

    out += table(
        [
            "Ticket", "Location", "Issue", "Priority",
            "Status", "Score", "Deadline"
        ],
        [
            [
                ticket_id(t), t["location"], t["description"],
                t["priority"], t["status"], priority_score(t),
                (
                    "OVERDUE" if is_overdue(t)
                    else t["due_at"][:16].replace("T", " ")
                )
            ]
            for t in queue
        ]
    )

    out += '</section>'

    # Search and filter.
    out += (
        '<section class="card tool-panel" id="search">'
        '<div class="card-head"><div><h2>⌕ Search & filter</h2>'
        '<p class="card-desc">Find tickets by keyword, category, '
        'status, or priority.</p></div></div>'
    )

    out += form(
        "search",
        [
            ("query", "Keyword (optional)", "text_optional", ""),
            ("category", "Category", "select", ["All"] + CATEGORIES),
            ("status", "Status", "select", ["All"] + STATUSES),
            ("priority", "Priority", "select", ["All"] + PRIORITIES)
        ],
        "Apply filters"
    )

    out += '</section>'

    # Update and reopen tickets.
    out += (
        '<section class="card tool-panel" id="manage">'
        '<div class="card-head"><div><h2>↻ Update a ticket</h2>'
        '<p class="card-desc">Record a status transition and leave '
        'an audit note.</p></div></div>'
    )

    out += form(
        "update",
        [
            ("id", "Ticket ID (e.g. CC-00104)", "text", ""),
            ("status", "New status", "select", STATUSES),
            ("comment", "Update note", "textarea", "")
        ],
        "Save status update"
    )

    out += (
        '<h2>Reopen a resolved ticket</h2>'
        + form(
            "reopen",
            [
                ("id", "Ticket ID", "text", ""),
                ("comment", "Why is it being reopened?", "text", "")
            ],
            "Reopen ticket"
        )
        + '</section>'
    )

    # Technician hub.
    techs = sorted({
        t["assigned_to"] for t in tickets if t.get("assigned_to")
    })

    tech_rows = []

    for tech in techs:
        mine = [t for t in tickets if t.get("assigned_to") == tech]
        open_count = sum(t["status"] != "Resolved" for t in mine)
        resolved_count = sum(t["status"] == "Resolved" for t in mine)

        tech_rows.append([
            tech, len(mine), open_count, resolved_count
        ])

    out += (
        '<section class="card tool-panel" id="technicians">'
        '<div class="card-head"><div><h2>♙ Technician hub</h2>'
        '<p class="card-desc">Assign tickets with workload visibility.'
        '</p></div></div>'
    )

    out += form(
        "assign",
        [
            ("id", "Ticket ID", "text", ""),
            ("technician", "Technician name", "text", "")
        ],
        "Assign technician"
    )

    out += heading("Current workload") + table(
        ["Technician", "Total assigned", "Open", "Resolved"],
        tech_rows
    )

    out += (
        '<div class="notice">Assignment suggestion: choose a '
        'technician with fewer open tickets and the right skills. '
        'This is a workload-based suggestion, not an automatic dispatch.'
        '</div></section>'
    )

    # Ticket history and comments.
    out += (
        '<section class="card tool-panel" id="history">'
        '<div class="card-head"><div><h2>◷ Ticket timeline</h2>'
        '<p class="card-desc">Review the full history and add an '
        'internal note.</p></div></div>'
    )

    out += form(
        "history",
        [("id", "Ticket ID", "text", "")],
        "Load ticket history"
    )

    out += form(
        "comment",
        [
            ("id", "Ticket ID", "text", ""),
            ("comment", "Timeline note", "textarea", "")
        ],
        "Add note to timeline"
    )

    out += '</section>'

    # Resolution feedback.
    out += (
        '<section class="card tool-panel" id="feedback">'
        '<div class="card-head"><div><h2>☆ Resolution feedback</h2>'
        '<p class="card-desc">Capture the reporter’s satisfaction '
        'after a ticket is resolved.</p></div></div>'
    )

    out += form(
        "feedback",
        [
            ("id", "Resolved ticket ID", "text", ""),
            ("rating", "Rating (1–5)", "select", ["5", "4", "3", "2", "1"]),
            ("feedback", "Feedback comment (optional)", "textarea_optional", "")
        ],
        "Save feedback"
    )

    rated = [t for t in tickets if t.get("rating")]
    average = (
        sum(int(t["rating"]) for t in rated) / len(rated)
        if rated else 0
    )

    out += (
        f'<div class="notice">Current satisfaction average: '
        f'<strong>{average:.1f}/5</strong> from {len(rated)} rating(s).'
        '</div></section>'
    )

    # All complaints.
    out += (
        '<section class="card tool-panel" id="all-complaints">'
        '<div class="card-head"><div><h2>▤ All complaints</h2>'
        '<p class="card-desc">Full ticket register with status, priority, '
        'and deadline.</p></div>'
        '<a class="btn primary" href="/export.csv">⇩ Download CSV</a>'
        '</div>'
    )

    out += table(
        [
            "Ticket", "Category", "Location", "Description",
            "Priority", "Status", "Technician", "Due"
        ],
        ticket_rows(
            sorted(
                tickets,
                key=lambda t: int(t.get("id", 0)),
                reverse=True
            )
        )
    )

    out += '</section>'

    # Analytics.
    category_counts = Counter(t["category"] for t in tickets)
    durations = []

    for ticket in tickets:
        if ticket["status"] != "Resolved":
            continue

        created = parse_dt(
            ticket.get("created_at", ticket.get("created"))
        )

        resolved_at = None

        for event in ticket.get("history", []):
            if (
                isinstance(event, dict)
                and event.get("event") == "Status changed to Resolved"
            ):
                resolved_at = parse_dt(event.get("time"))
                break

        if resolved_at:
            durations.append(
                max(0, (resolved_at - created).total_seconds() / 3600)
            )

    average_resolution = (
        sum(durations) / len(durations) if durations else None
    )

    out += (
        '<section class="card tool-panel" id="analytics">'
        '<div class="card-head"><div><h2>▥ Insights & reports</h2>'
        '<p class="card-desc">Understand repeat issues and help-desk '
        'performance.</p></div>'
        '<a class="btn primary" href="/export.csv">⇩ Export CSV</a>'
        '</div>'
    )

    out += heading("Issues by category")
    max_category = max(category_counts.values(), default=1)

    if category_counts:
        for category, count in category_counts.most_common():
            percentage = round(count / max_category * 100)

            out += (
                f'<div class="bar-row"><span>{esc(category)}</span>'
                f'<div class="bar-track"><div class="bar-fill" '
                f'style="width:{percentage}%"></div></div>'
                f'<strong>{count}</strong></div>'
            )
    else:
        out += (
            '<div class="notice">Category analytics will appear '
            'after tickets are registered.</div>'
        )

    repeats = Counter(
        (t["location"], t["category"]) for t in tickets
    )

    repeat_rows = [
        [location, category, count]
        for (location, category), count in repeats.most_common()
        if count > 1
    ]

    out += heading("Repeat-problem hotspots")
    out += table(
        ["Location", "Category", "Tickets recorded"],
        repeat_rows
    )

    out += heading("Performance snapshot")
    out += table(
        ["Metric", "Value"],
        [
            ["Total tickets", len(tickets)],
            ["Overdue open tickets", sum(is_overdue(t) for t in tickets)],
            ["Resolved", sum(t["status"] == "Resolved" for t in tickets)],
            [
                "Average resolution time (hours)",
                (
                    f"{average_resolution:.1f}"
                    if average_resolution is not None
                    else "Not enough resolution history yet"
                )
            ]
        ]
    )

    out += '</section>'

    # SLA settings.
    sla = int(store.data.get("settings", {}).get("sla_hours", 72))

    out += (
        '<section class="card tool-panel" id="settings">'
        '<div class="card-head"><div><h2>⚙ Help desk settings</h2>'
        '<p class="card-desc">Set the target response window used '
        'for new tickets.</p></div></div>'
    )

    out += form(
        "settings",
        [("sla_hours", "Target resolution time in hours", "number", str(sla))],
        "Save SLA settings"
    )

    out += (
        '<div class="notice">Changing this setting applies to new tickets. '
        'Existing ticket deadlines remain unchanged so their original '
        'targets are preserved.</div></section>'
    )

    out += (
        '<div class="footer">CampusCare · Smart Campus Complaint & '
        'Maintenance System · Local project data</div></main></div>'
    )

    # Show the selected status list directly when a dashboard KPI is clicked.
    out += r"""
    <script>
    (() => {
      const statusIds = ['pending-list', 'progress-list', 'resolved-list'];
      function showStatusList() {
        const id = window.location.hash.slice(1);
        const isStatusList = statusIds.includes(id);
        document.querySelectorAll('.status-section').forEach(section => {
          section.style.display = section.id === id ? 'block' : 'none';
        });
        document.querySelectorAll('.dashboard-only').forEach(section => {
          section.style.display = isStatusList ? 'none' : '';
        });
        if (isStatusList) {
          const selected = document.getElementById(id);
          if (selected) window.scrollTo({top: 0, behavior: 'smooth'});
        }
      }
      window.addEventListener('hashchange', showStatusList);
      document.addEventListener('DOMContentLoaded', showStatusList);
      showStatusList();
    })();
    </script>
    """

    return out

def handle(data):
    tickets = all_tickets()
    action = data.get("action", "")

    # Register a new complaint.
    if action == "add":
        category = data.get("category", "")
        location = data.get("location", "").strip()
        description = data.get("description", "").strip()
        priority = data.get("priority", "Medium")
        reporter = data.get("reporter", "").strip() or "Campus user"

        if category not in CATEGORIES:
            raise ValueError("Choose a valid category.")

        if priority not in PRIORITIES:
            raise ValueError("Choose a valid priority.")

        if not location or not description:
            raise ValueError(
                "Location and description are required."
            )

        if len(description) < 8:
            raise ValueError(
                "Please provide at least 8 characters describing the issue."
            )

        candidate = {
            "category": category,
            "location": location,
            "description": description
        }

        duplicates = possible_duplicates(candidate)

        new_id = max(
            (int(t.get("id", 0)) for t in tickets),
            default=0
        ) + 1

        due = now() + timedelta(
            hours=int(
                store.data.get("settings", {}).get("sla_hours", 72)
            )
        )

        ticket = {
            "id": new_id,
            **candidate,
            "priority": priority,
            "status": "Pending",
            "assigned_to": "",
            "reporter": reporter,
            "created": date.today().isoformat(),
            "created_at": stamp(),
            "due_at": due.isoformat(sep=" "),
            "history": [],
            "comments": [],
            "rating": None,
            "feedback": ""
        }

        add_history(
            ticket,
            "Ticket created",
            f"Created by {reporter}; priority {priority}"
        )

        tickets.append(ticket)
        store.data["tickets"] = tickets
        store.save()

        message = f"{ticket_id(ticket)} created successfully."

        if duplicates:
            matches = ", ".join(
                f"{ticket_id(t)} ({score:.0%} similarity)"
                for t, score in duplicates[:3]
            )

            message += (
                f" Similar unresolved tickets to review: {matches}. "
                "The new ticket was still saved."
            )

            return message, "warning"

        return message + " It is now in the Pending queue."

    # Search and filter complaints.
    if action == "search":
        query = data.get("query", "").strip().lower()
        category = data.get("category", "All")
        status = data.get("status", "All")
        priority = data.get("priority", "All")

        found = [
            t for t in tickets
            if (
                not query
                or query in " ".join(
                    str(t.get(key, ""))
                    for key in (
                        "id", "location", "description",
                        "assigned_to", "category"
                    )
                ).lower()
            )
            and (category == "All" or t["category"] == category)
            and (status == "All" or t["status"] == status)
            and (priority == "All" or t["priority"] == priority)
        ]

        if not found:
            return {
                "message": "No complaints matched those filters. Try another keyword or category.",
                "kind": "warning",
                "target": "search",
            }

        result_rows = ticket_rows(sorted(found, key=lambda t: int(t.get("id", 0)), reverse=True))
        return {
            "message": f"Found {len(found)} matching complaint(s). Results are shown below.",
            "kind": "success",
            "target": "search",
            "html": table(["Ticket", "Category", "Location", "Description", "Priority", "Status", "Technician", "Deadline"], result_rows),
        }

    # Update ticket status.
    if action == "update":
        ticket = find_ticket(data.get("id"))
        status = data.get("status", "")
        comment = data.get("comment", "").strip()

        if status not in STATUSES:
            raise ValueError("Choose a valid status.")

        if not comment:
            raise ValueError(
                "Add a short note explaining this status update."
            )

        old_status = ticket["status"]

        if old_status == "Resolved" and status != "Resolved":
            raise ValueError(
                "This ticket is resolved. Use Reopen ticket "
                "to reactivate it with a clear reason."
            )

        ticket["status"] = status

        add_history(
            ticket,
            f"Status changed to {status}",
            f"Previous status: {old_status}. {comment}"
        )

        ticket["comments"].append({
            "time": stamp(),
            "by": "Help Desk",
            "text": comment
        })

        store.save()

        return (
            f"{ticket_id(ticket)} updated: {old_status} → {status}. "
            "The timeline and dashboard are refreshed."
        )

    # Reopen a resolved ticket.
    if action == "reopen":
        ticket = find_ticket(data.get("id"))
        comment = data.get("comment", "").strip()

        if not comment:
            raise ValueError(
                "Explain why the ticket is being reopened."
            )

        if ticket["status"] != "Resolved":
            raise ValueError(
                "Only a resolved ticket can be reopened."
            )

        ticket["status"] = "Reopened"

        add_history(ticket, "Ticket reopened", comment)

        ticket["comments"].append({
            "time": stamp(),
            "by": "Help Desk",
            "text": comment
        })

        store.save()

        return (
            f"{ticket_id(ticket)} reopened and returned "
            "to the active queue."
        )

    # Assign a technician.
    if action == "assign":
        ticket = find_ticket(data.get("id"))
        technician = data.get("technician", "").strip()

        if not technician:
            raise ValueError("Enter a technician name.")

        old_technician = ticket.get("assigned_to", "") or "Unassigned"
        ticket["assigned_to"] = technician

        if ticket["status"] == "Pending":
            ticket["status"] = "Assigned"

        add_history(
            ticket,
            "Technician assigned",
            f"{old_technician} → {technician}"
        )

        store.save()

        open_count = sum(
            t.get("assigned_to") == technician
            and t.get("status") != "Resolved"
            for t in tickets
        )

        return (
            f"{ticket_id(ticket)} assigned to {technician}. "
            f"Their current open workload is {open_count} ticket(s)."
        )

    # Load a ticket's timeline.
    if action == "history":
        ticket = find_ticket(data.get("id"))
        events = ticket.get("history", [])

        if not events:
            return {
                "message": f"{ticket_id(ticket)} has no recorded timeline events yet.",
                "kind": "warning",
                "target": "history",
            }

        history_rows = []
        for event in events:
            if isinstance(event, dict):
                history_rows.append([event.get("time", ""), event.get("event", ""), event.get("details", "")])
            elif isinstance(event, (list, tuple)):
                history_rows.append(list(event))
        history_rows.extend([[item.get("time", ""), "Comment by " + item.get("by", "Help Desk"), item.get("text", "")] for item in ticket.get("comments", []) if isinstance(item, dict)])
        return {
            "message": f"Loaded {len(events)} timeline event(s) for {ticket_id(ticket)}. Full history is shown below.",
            "kind": "success",
            "target": "history",
            "html": table(["Time", "Event", "Details"], history_rows),
        }

    # Add a comment to the timeline.
    if action == "comment":
        ticket = find_ticket(data.get("id"))
        comment = data.get("comment", "").strip()

        if not comment:
            raise ValueError("Enter a note before saving.")

        ticket["comments"].append({
            "time": stamp(),
            "by": "Help Desk",
            "text": comment
        })

        add_history(ticket, "Note added", comment)
        store.save()

        return (
            f"Note added to {ticket_id(ticket)}. "
            "It is saved in the ticket timeline."
        )

    # Save satisfaction feedback.
    if action == "feedback":
        ticket = find_ticket(data.get("id"))

        if ticket["status"] != "Resolved":
            raise ValueError(
                "Feedback can be recorded after a ticket is resolved."
            )

        try:
            rating = int(data.get("rating", ""))
        except ValueError:
            raise ValueError("Choose a rating from 1 to 5.")

        if rating not in range(1, 6):
            raise ValueError("Rating must be between 1 and 5.")

        ticket["rating"] = rating
        ticket["feedback"] = data.get("feedback", "").strip()

        add_history(
            ticket,
            "Resolution feedback received",
            f"Rating: {rating}/5. {ticket['feedback']}"
        )

        store.save()

        return (
            f"Thanks! Feedback for {ticket_id(ticket)} "
            f"was saved ({rating}/5)."
        )

    # Configure SLA deadline for new complaints.
    if action == "settings":
        try:
            hours = int(data.get("sla_hours", ""))
        except ValueError:
            raise ValueError(
                "Enter the SLA as a whole number of hours."
            )

        if not 1 <= hours <= 8760:
            raise ValueError(
                "SLA must be between 1 and 8760 hours."
            )

        store.data.setdefault("settings", {})["sla_hours"] = hours
        store.save()

        return (
            f"SLA setting saved. New tickets now receive a "
            f"{hours}-hour target. Existing deadlines were preserved."
        )

    raise ValueError(
        "Unknown action. Please use a button in the application."
    )


def export_csv():
    output = io.StringIO(newline="")
    writer = csv.writer(output)

    writer.writerow([
        "Ticket ID", "Category", "Location", "Description",
        "Priority", "Status", "Technician", "Reporter",
        "Created", "Deadline", "Overdue", "Rating",
        "Feedback", "History"
    ])

    for ticket in sorted(
        all_tickets(),
        key=lambda item: int(item.get("id", 0))
    ):
        history_text = " | ".join(
            (
                f"{event.get('time', '')} "
                f"{event.get('event', '')}: "
                f"{event.get('details', '')}"
            )
            for event in ticket.get("history", [])
            if isinstance(event, dict)
        )

        writer.writerow([
            ticket_id(ticket),
            ticket["category"],
            ticket["location"],
            ticket["description"],
            ticket["priority"],
            ticket["status"],
            ticket["assigned_to"],
            ticket["reporter"],
            ticket.get("created_at", ticket.get("created", "")),
            ticket["due_at"],
            "Yes" if is_overdue(ticket) else "No",
            ticket.get("rating") or "",
            ticket.get("feedback", ""),
            history_text
        ])

    return output.getvalue()


if __name__ == "__main__":
    run(
        "CampusCare | Smart Campus Help Desk",
        view,
        handle,
        export_csv
    )