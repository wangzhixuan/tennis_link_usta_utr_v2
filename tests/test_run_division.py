import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.service import db
db.DB_PATH = os.path.join(tempfile.gettempdir(), "tennislink_test_run_division.db")
db.init_db()
db.reset_all()

import scripts.service.event_runner as er

print("=" * 60)
print("Division runner: single tournament run + new-player detection")
print("=" * 60)


class FakeUSTA:
    def __init__(self, players):
        self.players = players
        self.called = None

    def get_tournament_info(self, tid):
        return {"name": "Test Cup", "guid": "ABCD1234-0000-0000-0000-000000000000",
                "start_date": "2026-12-01"}

    def scrape_tournament(self, tid, division, headless=True, target_event_ids=None):
        self.called = (tid, division)
        return self.players

    def fetch_player_profile(self, usta_id, headless=True):
        return {"usta_id": usta_id, "name": f"Player {usta_id}", "city": "San Diego",
                "state": "CA", "wtn_singles": 30.0, "wtn_doubles": 29.0,
                "rankings": [], "gender": "MALE"}


class FakeMatcher:
    def __init__(self, utr):
        self.utr = utr

    def find_utr_profile(self, up, players=None, no_cross_ref=False, **kwargs):
        return {"utr_id": f"U{up['usta_id']}", "confidence": 0.9, "match_method": "name_geo"}

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


p1 = [
    {"usta_id": "1", "name": "Alice One", "city": "San Diego", "state": "CA", "status": "Accepted"},
    {"usta_id": "2", "name": "Bob Two", "city": "San Diego", "state": "CA", "status": "Accepted"},
]

tmpdir = tempfile.mkdtemp(prefix="tl_division_")
old = er.DATA_DIR
er.DATA_DIR = tmpdir

try:
    with patch.object(er, "PlayerMatcher", FakeMatcher):
        r1 = er.run_division("T1", "Boys U12", run_profiles=True, no_login=True,
                             usta=FakeUSTA(p1), utr=FakeUTR())
    assert r1["ok"], r1.get("error")
    df = r1["df"]
    assert len(df) == 2
    assert "New" in df.columns and "Status" in df.columns
    assert r1["ranking_label"] == "B12 Rank"
    assert df.iloc[0]["UTR"] == 4.0
    assert r1["new_count"] == 0 and r1["previous_count"] is None
    assert os.path.exists(r1["out_path"])
    print("[1/4] first run + TSV: PASSED")

    p2 = p1 + [{"usta_id": "3", "name": "Cara Three", "city": "San Diego",
                "state": "CA", "status": "Accepted"}]
    with patch.object(er, "PlayerMatcher", FakeMatcher):
        r2 = er.run_division("T1", "Boys U12", run_profiles=True, no_login=True,
                             usta=FakeUSTA(p2), utr=FakeUTR())
    assert r2["ok"] and r2["new_count"] == 1 and r2["previous_count"] == 2
    assert r2["df"][r2["df"]["USTA ID"] == "3"].iloc[0]["New"] == "NEW"
    print("[2/4] new-player detection: PASSED")

    from scripts.service.db import get_checked_events
    checked = {e["event_key"]: e for e in get_checked_events()}
    assert "ABCD1234-0000-0000-0000-000000000000:division:Boys U12" in checked
    assert checked["ABCD1234-0000-0000-0000-000000000000:division:Boys U12"]["player_count"] == 3
    print("[3/4] checked event saved: PASSED")

    print("\n[4/4] Division save merges into an existing event row...")

    class FakeUSTATwo(FakeUSTA):
        def get_tournament_info(self, tid):
            return {"name": "Cup Two", "guid": "99999999-0000-0000-0000-000000000000",
                    "start_date": "2026-12-01"}

    guid2 = "99999999-0000-0000-0000-000000000000"
    from scripts.service.db import save_checked_event, get_checked_events
    save_checked_event(
        f"{guid2}:E1", guid2, "E1", "Cup Two", "Singles Boys U12", "Level 6",
        {"gender": "BOYS", "eventType": "SINGLES", "ageCategory": {"todsCode": "U12"}},
        [{"usta_id": "1", "name": "Alice One", "status": "Accepted"},
         {"usta_id": "2", "name": "Bob Two", "status": "Accepted"}],
        event_date="2026-12-01",
    )
    with patch.object(er, "PlayerMatcher", FakeMatcher):
        r3 = er.run_division("T2", "Boys U12", run_profiles=True, no_login=True,
                             usta=FakeUSTATwo(p2), utr=FakeUTR())
    assert r3["ok"] and r3["new_count"] == 1 and r3["previous_count"] == 2, r3
    rows = [e for e in get_checked_events() if e["tournament_id"] == guid2]
    assert len(rows) == 1, [r["event_key"] for r in rows]
    assert rows[0]["event_id"] == "E1"
    assert rows[0]["player_count"] == 3
    assert rows[0]["division"].get("gender") == "BOYS", rows[0]["division"]
    assert rows[0]["division"].get("ageCategory", {}).get("todsCode") == "U12", rows[0]["division"]

    save_checked_event(
        f"{guid2}:division:Boys U12", guid2, None, "Cup Two", "Singles Boys U12",
        None, {}, [], event_date="2026-12-01",
    )
    rows = [e for e in get_checked_events() if e["tournament_id"] == guid2]
    assert rows[0]["division"].get("gender") == "BOYS", rows[0]["division"]
    print("  PASSED")
finally:
    er.DATA_DIR = old
    try:
        os.remove(db.DB_PATH)
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
print("Division runner: ALL TESTS PASSED")
print("=" * 60)
