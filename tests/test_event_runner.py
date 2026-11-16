import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.service import db
db.DB_PATH = os.path.join(tempfile.gettempdir(), "tennislink_test_event_runner.db")
db.init_db()
db.reset_all()

import scripts.service.event_runner as er

print("=" * 60)
print("Event runner: one event through the full pipeline")
print("=" * 60)


class FakeUSTA:
    def __init__(self):
        self.called = None

    def get_tournament_info(self, tid):
        return {"name": "Test Cup", "guid": "ABCD1234-0000-0000-0000-000000000000",
                "start_date": "2026-12-01"}

    def scrape_tournament(self, tid, division, headless=True, target_event_ids=None):
        self.called = (tid, division, target_event_ids)
        return [
            {"usta_id": "1", "name": "Alice One", "city": "San Diego", "state": "CA",
             "status": "Accepted"},
            {"usta_id": "2", "name": "Bob Two", "city": "San Diego", "state": "CA",
             "status": "Withdrawn"},
        ]

    def fetch_player_profile(self, usta_id, headless=True):
        return {"usta_id": usta_id, "name": f"Player {usta_id}", "city": "San Diego",
                "state": "CA", "wtn_singles": 30.0, "wtn_doubles": 29.0,
                "rankings": [], "gender": "MALE"}


class FakeMatcher:
    def __init__(self, utr):
        self.utr = utr

    def find_utr_profile(self, up, players=None, no_cross_ref=False, **kwargs):
        return {"utr_id": f"U{up['usta_id']}", "confidence": 0.9,
                "match_method": "name_geo"}

    def rematch_player(self, *a, **k):
        return None


class FakeUTR:
    def __init__(self):
        self.last_profile_not_found = False

    def login_if_needed(self):
        return True

    def get_player_profile(self, utr_id):
        return {"utr_id": utr_id, "name": "X", "utr_singles": 4.0,
                "utr_doubles": 3.0, "city": "San Diego", "state": "CA"}


event = {"id": "E1", "label": "Singles Boys U12",
         "division": {"gender": "BOYS", "eventType": "SINGLES",
                      "ageCategory": {"todsCode": "U12"}}}

tmpdir = tempfile.mkdtemp(prefix="tl_runner_")
old_data_dir = er.DATA_DIR
er.DATA_DIR = tmpdir

try:
    with patch.object(er, "PlayerMatcher", FakeMatcher):
        res = er.run_event("T1", event, run_profiles=True, no_login=True,
                           usta=FakeUSTA(), utr=FakeUTR())
except Exception:
    er.DATA_DIR = old_data_dir
    raise

assert res["ok"], res.get("error")
df = res["df"]
print("\n[1/6] result shape...")
assert len(df) == 2
assert "Status" in df.columns and "WTN" in df.columns
assert res["ranking_label"] == "B12 Rank", res["ranking_label"]
print("  PASSED")

print("\n[2/6] values...")
assert df.iloc[0]["Status"] == "Accepted"
assert df.iloc[1]["Status"] == "Withdrawn"
assert df.iloc[0]["WTN"] == 30.0
assert df.iloc[0]["UTR"] == 4.0 and df.iloc[1]["UTR"] == 4.0
assert df.iloc[0]["UTR ID"] == "U1"
print("  PASSED")

print("\n[3/6] exact event filtering passed to scraper...")
fake = FakeUSTA()
with patch.object(er, "PlayerMatcher", FakeMatcher):
    res2 = er.run_event("T1", event, run_profiles=True, no_login=True,
                        usta=fake, utr=FakeUTR())
assert fake.called is not None
assert fake.called[2] == {"E1"}, fake.called
print("  PASSED")

print("\n[4/6] TSV written...")
assert os.path.exists(res2["out_path"])
import pandas as pd
back = pd.read_csv(res2["out_path"], sep="\t")
assert len(back) == 2 and "Status" in back.columns
print("  PASSED")

print("\n[5/6] New-player detection on re-check...")
from scripts.service.db import get_checked_events

event2 = {"id": "E2", "label": "Singles Girls U12",
          "division": {"gender": "GIRLS", "eventType": "SINGLES",
                       "ageCategory": {"todsCode": "U12"}}}


class FakeUSTAPlayers(FakeUSTA):
    def __init__(self, players):
        super().__init__()
        self.players = players

    def scrape_tournament(self, tid, division, headless=True, target_event_ids=None):
        self.called = (tid, division, target_event_ids)
        return self.players


p1 = [
    {"usta_id": "1", "name": "Alice One", "city": "San Diego", "state": "CA",
     "status": "Accepted"},
    {"usta_id": "2", "name": "Bob Two", "city": "San Diego", "state": "CA",
     "status": "Accepted"},
]
with patch.object(er, "PlayerMatcher", FakeMatcher):
    r1 = er.run_event("T2", event2, run_profiles=True, no_login=True,
                      usta=FakeUSTAPlayers(p1), utr=FakeUTR())
assert r1["ok"] and r1["new_count"] == 0 and r1["previous_count"] is None

p2 = p1 + [{"usta_id": "3", "name": "Cara Three", "city": "San Diego",
            "state": "CA", "status": "Accepted"}]
with patch.object(er, "PlayerMatcher", FakeMatcher):
    r2 = er.run_event("T2", event2, run_profiles=True, no_login=True,
                      usta=FakeUSTAPlayers(p2), utr=FakeUTR())
assert r2["ok"] and r2["new_count"] == 1 and r2["previous_count"] == 2
assert "New" in r2["df"].columns
assert r2["df"][r2["df"]["USTA ID"] == "3"].iloc[0]["New"] == "NEW"
assert r2["df"][r2["df"]["USTA ID"] == "1"].iloc[0]["New"] == ""

checked = {e["event_key"]: e for e in get_checked_events()}
assert "T2:E2" in checked and checked["T2:E2"]["player_count"] == 3
print("  PASSED")

print("\n[6/6] Empty division falls back to the event label...")
class FakeUSTARanked(FakeUSTA):
    def fetch_player_profile(self, usta_id, headless=True):
        return {"usta_id": usta_id, "name": f"Player {usta_id}", "city": "San Diego",
                "state": "CA", "wtn_singles": 30.0, "wtn_doubles": 29.0,
                "gender": "FEMALE",
                "rankings": [{"display_label": "Girls' 14 National Standings List (combined)",
                              "points": 484}]}

event3 = {"id": "E3", "label": "Singles Girls U14", "division": {}}
with patch.object(er, "PlayerMatcher", FakeMatcher):
    r3 = er.run_event("T3", event3, run_profiles=True, no_login=True,
                      usta=FakeUSTARanked(), utr=FakeUTR())
assert r3["ok"], r3.get("error")
assert r3["ranking_label"] == "G14 Rank", r3["ranking_label"]
assert int(r3["df"].iloc[0]["G14 Rank"]) == 484, r3["df"].iloc[0].to_dict()
assert "Singles_Girls_U14" in os.path.basename(r3["out_path"]), r3["out_path"]
print("  PASSED")

er.DATA_DIR = old_data_dir

# cleanup
for p in (db.DB_PATH,):
    try:
        os.remove(p)
    except OSError:
        pass
for f in os.listdir(tmpdir):
    try:
        os.remove(os.path.join(tmpdir, f))
    except OSError:
        pass
try:
    os.rmdir(tmpdir)
except OSError:
    pass

print("\n" + "=" * 60)
print("Event runner: ALL TESTS PASSED")
print("=" * 60)
