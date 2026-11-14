import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd

print("=" * 60)
print("Results table rendering: pinned # column")
print("=" * 60)

from scripts.render import build_results_html, short_ranking_label

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

print("\n" + "=" * 60)
print("Results table rendering: ALL TESTS PASSED")
print("=" * 60)
