"""Streamlit-free HTML rendering helpers (unit-testable)."""
import datetime
import html
import math
import re

import pandas as pd


# Distinct, dark-enough colors for white text
LEVEL_COLORS = {
    1: "#5e35b1",  # deep purple
    2: "#8e24aa",  # purple
    3: "#c2185b",  # pink
    4: "#d84315",  # orange-red
    5: "#a66b00",  # dark amber
    6: "#2e7d32",  # green
    7: "#1565c0",  # blue
}
UNKNOWN_LEVEL_COLOR = "#607d8b"


def format_duration(seconds):
    """Format a number of seconds as 'X min Y sec'."""
    try:
        total = max(0, int(seconds))
    except (TypeError, ValueError):
        total = 0
    minutes, secs = divmod(total, 60)
    return f"{minutes} min {secs} sec"


def order_checked_events(events, include_past=False, today=None):
    """
    Order checked events by tournament date: upcoming first (soonest first), then
    past events (most recent first) when include_past is True. Defaults to future only.
    """
    today = today or datetime.date.today()

    def event_day(e):
        raw = (e.get("event_date") or "")[:10]
        try:
            return datetime.date.fromisoformat(raw)
        except (TypeError, ValueError):
            return None

    future = [e for e in events if event_day(e) is None or event_day(e) >= today]
    future.sort(key=lambda e: (event_day(e) is None,
                               event_day(e) or datetime.date.max,
                               e.get("last_checked") or ""))
    if not include_past:
        return future

    past = [e for e in events if event_day(e) is not None and event_day(e) < today]
    past.sort(key=event_day, reverse=True)
    return future + past


def level_color(level_name):
    """Return a distinct color for a USTA level name (Open/Closed share the number)."""
    m = re.search(r"level\s*(\d+)", level_name or "", re.I)
    if m:
        return LEVEL_COLORS.get(int(m.group(1)), UNKNOWN_LEVEL_COLOR)
    return UNKNOWN_LEVEL_COLOR


def distance_color(distance, reference_miles=100.0):
    """
    Green -> red color based on log(distance), clamped to a reference distance.

    The closer the tournament, the greener (hue 120°); farther is redder (hue 0°).
    """
    if distance is None:
        return "#9e9e9e"
    try:
        d = max(0.0, float(distance))
    except (TypeError, ValueError):
        return "#9e9e9e"
    denom = math.log1p(max(1.0, float(reference_miles)))
    t = max(0.0, min(1.0, math.log1p(d) / denom))
    hue = 120.0 * (1.0 - t)
    return f"hsl({hue:.0f}, 70%, 38%)"


def tournament_header_html(tournament, date_range_text):
    """Colored, always-visible tournament header (date • level chip • distance chip • name)."""
    level = tournament.get("level") or ""
    dist = tournament.get("distance")
    dist_text = f"{float(dist):.1f} mi" if dist is not None else ""
    lc = level_color(level)
    dc = distance_color(dist)
    return (
        "<div style='display:flex;align-items:center;gap:8px;flex-wrap:wrap;"
        "margin:6px 0 2px 0;'>"
        f"<span style='font-weight:600'>{html.escape(date_range_text)}</span>"
        f"<span style='background:{lc};color:#fff;padding:1px 8px;border-radius:10px;"
        f"font-size:12px;white-space:nowrap'>{html.escape(level)}</span>"
        f"<span style='background:{dc};color:#fff;padding:1px 8px;border-radius:10px;"
        f"font-size:12px;white-space:nowrap'>{html.escape(dist_text)}</span>"
        f"<span style='font-size:15px'>{html.escape(str(tournament.get('name') or ''))}</span>"
        "</div>"
    )


def short_ranking_label(division):
    """
    Compact ranking column header (<=10 chars), e.g. 'G14 Rank', 'B18 Rank'.

    Handles Boys/Girls and any age, with or without a 'U' prefix:
      "Girls U12" -> "G12 Rank", "Boys' 18" -> "B18 Rank", "U14" -> "U14 Rank".
    """
    if not division:
        return None
    parts = str(division).replace("'", " ").split()
    gender = None
    age = None
    for p in parts:
        pl = p.lower()
        if pl in ("girls", "girl"):
            gender = "G"
        elif pl in ("boys", "boy"):
            gender = "B"
        elif pl.startswith("u") and pl[1:].isdigit():
            age = pl[1:]
        elif pl.isdigit():
            age = pl
    if gender and age:
        return f"{gender}{age} Rank"
    if gender:
        return f"{gender} Rank"
    if age:
        return f"U{age} Rank"
    return None


def build_results_html(display_df, col_order, ranking_label):
    """
    Build a static HTML results table with a pinned (position: sticky) '#' column.

    Because the table is static, sorting is done server-side; the '#' column is always
    numbered 1..N in the current display order and stays pinned when scrolling.
    """
    headers = {
        "#": "#",
        "Player Name": "Player Name",
        "New": "New",
        "Status": "Status",
        "Gender": "Gender",
        "WTN": "WTN",
        ranking_label: ranking_label,
        "Profile Updated": "Profiled",
        "UTR": "UTR Rtng",
        "UTR Updated": "UTR Profiled",
        "Hometown": "Hometown",
        "USTA Profile": "USTA",
        "UTR Profile": "UTR",
    }

    def cell_text(col, val):
        if val is None:
            return ""
        try:
            if pd.isna(val):
                return ""
        except (TypeError, ValueError):
            pass
        if col in ("WTN", "UTR"):
            try:
                return f"{float(val):.2f}" if col == "UTR" else f"{float(val):.1f}"
            except (TypeError, ValueError):
                return str(val)
        if col in ("USTA ID", "UTR ID", "#", "Player Name", "Hometown",
                   "Status", "Gender", "Profile Updated", "UTR Updated"):
            return str(val)
        try:
            f = float(val)
            return f"{int(f)}" if f.is_integer() else f"{f:g}"
        except (TypeError, ValueError):
            return str(val)

    head = "".join(f"<th>{html.escape(headers.get(c, c))}</th>" for c in col_order)
    body = []
    for _, row in display_df.iterrows():
        is_new = str(row.get("New") or "").strip().lower() not in ("", "nan", "none")
        cells = []
        for c in col_order:
            v = row.get(c)
            if c == "New":
                badge = "<span class='tl-new-badge'>NEW</span>" if is_new else ""
                cells.append(f"<td>{badge}</td>")
            elif c == "USTA Profile" and v:
                cells.append(f"<td><a href='{html.escape(str(v))}' target='_blank'>USTA</a></td>")
            elif c == "UTR Profile" and v:
                cells.append(f"<td><a href='{html.escape(str(v))}' target='_blank'>UTR</a></td>")
            else:
                cls = " class='tl-rank'" if c == "#" else ""
                cells.append(f"<td{cls}>{html.escape(cell_text(c, v))}</td>")
        tr_cls = " class='tl-new-row'" if is_new else ""
        body.append(f"<tr{tr_cls}>" + "".join(cells) + "</tr>")

    style = """
    <style>
    .tl-wrap { overflow:auto; max-height:70vh; border:1px solid #e3e3e8; border-radius:8px; }
    table.tl-table { border-collapse:separate; border-spacing:0; width:100%; font-size:13px; }
    .tl-table th, .tl-table td { padding:6px 10px; border-bottom:1px solid #eee; white-space:nowrap; text-align:left; }
    .tl-table thead th { position:sticky; top:0; background:#f6f6f8; z-index:2; }
    .tl-table td.tl-rank, .tl-table th:first-child {
        position:sticky; left:0; background:#fff; z-index:3; font-weight:600; color:#555;
        border-right:1px solid #e3e3e8;
    }
    .tl-table thead th:first-child { z-index:4; background:#f6f6f8; }
    .tl-table tbody tr:hover td { background:#fafafa; }
    .tl-table tbody tr:hover td.tl-rank { background:#fafafa; }
    .tl-table tbody tr.tl-new-row td { background:#eefaf0; }
    .tl-table tbody tr.tl-new-row td.tl-rank { background:#eefaf0; }
    .tl-new-badge { background:#137333; color:#fff; padding:1px 6px; border-radius:8px;
        font-size:11px; font-weight:700; letter-spacing:0.3px; }
    .tl-table a { color:#1a73e8; text-decoration:none; }
    </style>
    """
    return (
        style
        + "<div class='tl-wrap'><table class='tl-table'><thead><tr>"
        + head
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )
