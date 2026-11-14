"""Streamlit-free HTML rendering helpers (unit-testable)."""
import html

import pandas as pd


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
        cells = []
        for c in col_order:
            v = row.get(c)
            if c == "USTA Profile" and v:
                cells.append(f"<td><a href='{html.escape(str(v))}' target='_blank'>USTA</a></td>")
            elif c == "UTR Profile" and v:
                cells.append(f"<td><a href='{html.escape(str(v))}' target='_blank'>UTR</a></td>")
            else:
                cls = " class='tl-rank'" if c == "#" else ""
                cells.append(f"<td{cls}>{html.escape(cell_text(c, v))}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")

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
