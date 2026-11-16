import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd

print("=" * 60)
print("Results table rendering: pinned # column")
print("=" * 60)

from scripts.service.render import (
    build_results_html, short_ranking_label, level_color, distance_color,
    tournament_header_html, format_duration, order_checked_events,
)

rlabel = "Girls' 14 National Standings List (combined)"
df = pd.DataFrame([
    {"#": 1, "Player Name": "Alice <b>Smith</b>", "Status": "Accepted", "Gender": "F",
     "WTN": 22.9, "UTR": 6.8, "UTR ID": "2791433",
     rlabel: 350,
     "USTA Profile": "https://www.usta.com/en/home/play/player-search/profile.html#?uaid=1",
     "UTR Profile": "https://app.utrsports.net/profiles/2791433"},
    {"#": 2, "Player Name": "Bob Jones", "Status": "Withdrawn", "Gender": "M",
     "WTN": 30.0, "UTR": "Unrated", "UTR ID": "5213623",
     rlabel: 120,
     "USTA Profile": "https://www.usta.com/en/home/play/player-search/profile.html#?uaid=2",
     "UTR Profile": "https://app.utrsports.net/profiles/5213623"},
])
order = ["#", "Player Name", "Status", "Gender", "WTN", rlabel, "UTR", "Hometown",
         "USTA Profile", "UTR Profile"]

html = build_results_html(df, order, rlabel)

# Pinned first column + sticky header
assert "position:sticky" in html and "left:0" in html
assert "tl-rank" in html
# # is the first header cell
assert html.index("<th>#</th>") < html.index("<th>Player Name</th>")
# Values formatted
assert "22.90" not in html and "22.9" in html  # WTN 1 decimal
assert "6.80" in html or "6.8" in html
assert "Unrated" in html
# Links rendered
assert "app.utrsports.net/profiles/2791433" in html
assert ">USTA</a>" in html and ">UTR</a>" in html
# HTML escaping
assert "&lt;b&gt;Smith&lt;/b&gt;" in html and "<b>Smith</b>" not in html
print("  PASSED")

# Short ranking column header (<= 10 chars, any gender/age)
assert short_ranking_label("Girls U12") == "G12 Rank"
assert short_ranking_label("Boys 18") == "B18 Rank"
assert short_ranking_label("Singles Girls U14") == "G14 Rank"
assert short_ranking_label("Girls' 14") == "G14 Rank"
assert short_ranking_label("U14") == "U14 Rank"
assert short_ranking_label("Girls' 14 National Standings List (combined)") == "G14 Rank"
assert short_ranking_label("") is None
for d in ["Girls U12", "Boys 18", "Singles Boys U10",
          "Girls' 12 National Standings List (combined)"]:
    label = short_ranking_label(d)
    assert label is not None and len(label) <= 10, f"{d} -> {label} ({len(label)})"
print("  Short ranking label: PASSED")

# Level colors + log-scale distance color
assert level_color("Level 1") != level_color("Level 7")
assert level_color("Level 5 Open") == level_color("Level 5 Closed")
assert level_color("Level 6") == level_color("Level 6") != level_color("Level 4 Open")
assert level_color("Unsanctioned") == "#607d8b"

import re as _re
hue = lambda c: int(_re.search(r"hsl\((\d+)", c).group(1))
assert hue(distance_color(0)) == 120       # green
assert hue(distance_color(100)) == 0       # red
assert 0 < hue(distance_color(10)) < 120   # in between
assert distance_color(None) == "#9e9e9e"
print("  Level/distance colors: PASSED")

# Colored tournament header
header = tournament_header_html(
    {"name": "Seal Beach <Open>", "level": "Level 5 Open", "distance": 12.3},
    "Nov 29, 2026 – Dec 01, 2026",
)
assert "Nov 29, 2026" in header
assert level_color("Level 5 Open") in header
assert distance_color(12.3) in header
assert "&lt;Open&gt;" in header and "<Open>" not in header
print("  Tournament header: PASSED")

assert format_duration(0) == "0 min 0 sec"
assert format_duration(61) == "1 min 1 sec"
assert format_duration(299) == "4 min 59 sec"
print("  Duration format: PASSED")

# New-player column: badge + row highlight
new_df = pd.DataFrame([
    {"#": 1, "Player Name": "Alice", "New": "", "Status": "Accepted"},
    {"#": 2, "Player Name": "Bob", "New": "NEW", "Status": "Accepted"},
])
new_html = build_results_html(new_df, ["#", "Player Name", "New", "Status"], "Rank Pts")
assert "tl-new-badge" in new_html and "tl-new-row" in new_html
assert "<th>New</th>" in new_html
print("  New-player badge/row: PASSED")

# History ordering: future by date asc, past hidden unless requested
import datetime as _dt
today = _dt.date(2026, 11, 14)
events = [
    {"event_date": "2026-11-20", "last_checked": "2026-11-01"},
    {"event_date": "2026-11-15", "last_checked": "2026-11-02"},
    {"event_date": "2026-10-01", "last_checked": "2026-09-30"},
    {"event_date": "2026-12-01", "last_checked": "2026-11-03"},
    {"event_date": None, "last_checked": "2026-11-04"},
]
ordered = order_checked_events(events, include_past=False, today=today)
assert [e["event_date"] for e in ordered] == ["2026-11-15", "2026-11-20", "2026-12-01", None]
ordered_all = order_checked_events(events, include_past=True, today=today)
assert ordered_all[-1]["event_date"] == "2026-10-01"
print("  Checked-event date ordering: PASSED")

print("\n" + "=" * 60)
print("Results table rendering: ALL TESTS PASSED")
print("=" * 60)
