import os
import sys
import tempfile
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Scratch DB so the real tennislink.db is untouched
from scripts import db
db.DB_PATH = os.path.join(tempfile.gettempdir(), "tennislink_test_bootstrap.db")
db.init_db()
db.reset_all()

print("=" * 60)
print("Bootstrap: mapping DB seeding + match-history correlation")
print("=" * 60)

from scripts.matcher import PlayerMatcher
from scripts.utr_scraper import UTRScraper
from scripts.usta_scraper import USTAScraper
from scripts.bootstrap import bootstrap_from_seed
from scripts.db import get_mapping, count_mappings


# ── Fakes ──────────────────────────────────────────────────────────────
class FakeUSTA:
    def __init__(self, profiles, matches):
        self.profiles = profiles
        self.matches = matches
        self.profile_calls = []

    def fetch_player_profile(self, usta_id, **kwargs):
        self.profile_calls.append(str(usta_id))
        return self.profiles.get(str(usta_id), {})

    def get_player_matches(self, usta_id, **kwargs):
        return self.matches.get(str(usta_id), [])


class FakeUTR:
    def __init__(self, profiles, histories):
        self.profiles = profiles
        self.histories = histories
        self.profile_calls = []

    def get_player_profile(self, utr_id, **kwargs):
        self.profile_calls.append(str(utr_id))
        return self.profiles.get(str(utr_id), {})

    def get_player_match_history(self, utr_id, **kwargs):
        return self.histories.get(str(utr_id), [])


matcher = PlayerMatcher(UTRScraper())


# 1. Score/date normalization
print("\n[1/7] Testing score/date normalization...")
assert PlayerMatcher._normalize_score("6-3 6-2") == PlayerMatcher._normalize_score("6-2 6-3")
assert PlayerMatcher._normalize_score("7-5 3-6 [10-7]") == PlayerMatcher._normalize_score("3-6 7-5")
assert PlayerMatcher._normalize_score("") == ()
assert PlayerMatcher._match_date("2026-01-02T00:00:00") is not None
assert PlayerMatcher._match_date(None) is None
print("  PASSED")


# 2. Correlation: date+score+name -> confident pair
print("\n[2/7] Testing pair_from_match_histories...")
usta_matches = [
    {"opponent_usta_id": "A", "opponent_name": "Alice Smith", "date": "2026-01-01",
     "score": "6-3 6-2", "event": "Cup"},
    {"opponent_usta_id": "B", "opponent_name": "Bob Jones", "date": "2026-01-02",
     "score": "6-4 6-4", "event": "Cup"},
    {"opponent_usta_id": "C", "opponent_name": "Nobody Here", "date": "2026-01-03",
     "score": "6-0 6-0", "event": "Cup"},
]
utr_matches = [
    {"opponent_utr_id": "TA", "opponent_name": "Smith, Alice", "date": "2026-01-01", "score": "6-3 6-2"},
    {"opponent_utr_id": "TB", "opponent_name": "Jones, Bob", "date": "2026-01-02", "score": "6-4 6-4"},
    {"opponent_utr_id": "TC", "opponent_name": "Different Person", "date": "2026-01-05", "score": "6-0 6-0"},
]
pairs = matcher.pair_from_match_histories(usta_matches, utr_matches)
found = {(p["usta_id"], p["utr_id"]) for p in pairs}
assert ("A", "TA") in found, f"Expected A-TA, got {found}"
assert ("B", "TB") in found, f"Expected B-TB, got {found}"
assert ("C", "TC") not in found, "Mismatched date should not pair"
print(f"  Pairs: {sorted(found)}: PASSED")

# Wrong score -> no pair
no_score = [{"opponent_utr_id": "TA", "opponent_name": "Smith, Alice", "date": "2026-01-01", "score": "6-0 6-0"}]
assert matcher.pair_from_match_histories(usta_matches[:1], no_score) == []
print("  Score mismatch rejected: PASSED")

# Name mismatch but matching residence -> pair
usta_prof = {"A2": {"city": "Austin", "state": "TX"}}
utr_prof = {"TA2": {"city": "Austin", "state": "TX"}}
um = [{"opponent_usta_id": "A2", "opponent_name": "Alexander Smythe", "date": "2026-02-01", "score": "6-1 6-1"}]
tm = [{"opponent_utr_id": "TA2", "opponent_name": "Sasha Othername", "date": "2026-02-01", "score": "6-1 6-1"}]
res = matcher.pair_from_match_histories(um, tm, usta_profile_by_id=usta_prof, utr_profile_by_id=utr_prof)
assert len(res) == 1, "Same-date/score + strong residence should still pair"
print("  Residence corroboration: PASSED")
print("  PASSED")


# 3. bootstrap_from_seed: seed + one layer
print("\n[3/7] Testing bootstrap_from_seed (single layer)...")
db.reset_all()
profiles = {
    "S1": {"usta_id": "S1", "name": "Seed Player", "city": "LA", "state": "CA"},
    "A": {"usta_id": "A", "name": "Alice Smith", "city": "Austin", "state": "TX"},
    "B": {"usta_id": "B", "name": "Bob Jones", "city": "Dallas", "state": "TX"},
}
utr_profiles = {
    "T1": {"utr_id": "T1", "name": "Seed Player", "city": "LA", "state": "CA"},
    "TA": {"utr_id": "TA", "name": "Alice Smith", "city": "Austin", "state": "TX"},
    "TB": {"utr_id": "TB", "name": "Bob Jones", "city": "Dallas", "state": "TX"},
}
usta_matches_by_id = {
    "S1": [
        {"opponent_usta_id": "A", "opponent_name": "Alice Smith", "date": "2026-01-01",
         "score": "6-3 6-2", "event": "Cup"},
        {"opponent_usta_id": "B", "opponent_name": "Bob Jones", "date": "2026-01-02",
         "score": "6-4 6-4", "event": "Cup"},
    ],
    "A": [], "B": [],
}
utr_history_by_id = {
    "T1": [
        {"opponent_utr_id": "TA", "opponent_name": "Smith, Alice", "date": "2026-01-01", "score": "6-3 6-2"},
        {"opponent_utr_id": "TB", "opponent_name": "Jones, Bob", "date": "2026-01-02", "score": "6-4 6-4"},
    ],
    "TA": [], "TB": [],
}
fake_usta = FakeUSTA(profiles, usta_matches_by_id)
fake_utr = FakeUTR(utr_profiles, utr_history_by_id)

res = bootstrap_from_seed("S1", "T1", max_players=30, max_depth=1,
                          usta=fake_usta, utr=fake_utr, matcher=matcher)
assert res["ok"], res.get("error")
assert count_mappings() == 3, f"Expected 3 mappings, got {count_mappings()}"
assert get_mapping("S1")["utr_id"] == "T1"
assert get_mapping("A")["utr_id"] == "TA"
assert get_mapping("B")["utr_id"] == "TB"
assert get_mapping("S1")["match_method"] == "manual_seed"
print(f"  Mapped {res['added']} players: PASSED")


# 4. max_players cap (and bounded enrichment work)
print("\n[4/7] Testing max_players cap...")
db.reset_all()
capped_usta = FakeUSTA(profiles, usta_matches_by_id)
capped_utr = FakeUTR(utr_profiles, utr_history_by_id)
res = bootstrap_from_seed("S1", "T1", max_players=2, max_depth=1,
                          usta=capped_usta, utr=capped_utr, matcher=matcher)
assert count_mappings() == 2, f"Expected cap at 2, got {count_mappings()}"
# Seed profile + at most `remaining` candidate profiles -> bounded, no over-fetch
assert len(capped_usta.profile_calls) <= 2, f"Over-fetched USTA profiles: {capped_usta.profile_calls}"
assert len(capped_utr.profile_calls) <= 2, f"Over-fetched UTR profiles: {capped_utr.profile_calls}"
print("  PASSED")


# 5. Bad seed (names don't match)
print("\n[5/7] Testing bad seed rejection...")
db.reset_all()
bad_utr = FakeUTR({"T9": {"utr_id": "T9", "name": "Completely Different", "city": "NY", "state": "NY"}}, {})
res = bootstrap_from_seed("S1", "T9", usta=fake_usta, utr=bad_utr, matcher=matcher)
assert res["ok"] is False
assert count_mappings() == 0, "Bad seed must not write mappings"
print(f"  Rejected: {res['error']}: PASSED")


# 6. USTA playhistory + UTR history parsing (synthetic payloads)
print("\n[6/7] Testing scrapers' match parsing...")
usta = USTAScraper()
playhistory_resp = MagicMock(status_code=200)
playhistory_resp.json.return_value = {
    "events": [{
        "name": "Anaheim, CA", "division": "Girls' 12 singles", "matchFormat": "Singles",
        "rounds": [{
            "matchDate": "2026-09-13T16:00:00.000Z", "results": "1-6 1-6", "outcome": "L",
            "roundName": "E-Final", "opponents": [{"uaid": "2018579279", "name": "Kira ELLIOTT"}],
        }],
    }]
}
with patch.object(usta, "_get_usta_bearer_token", return_value="tok"), \
     patch.object(usta.session, "post", return_value=playhistory_resp):
    um = usta.get_player_matches("2018671404")
assert len(um) == 1
assert um[0]["opponent_usta_id"] == "2018579279"
assert um[0]["score"] == "1-6 1-6"
assert um[0]["win"] is False

utr = UTRScraper()
utr._jwt_cache = "tok"
v4_resp = MagicMock(status_code=200)
v4_resp.json.return_value = {
    "events": [{
        "name": "Anaheim, CA",
        "draws": [{"results": [{
            "players": {
                "winner1": {"id": "1", "firstName": "Eva", "lastName": "Yang", "singlesUtr": 4.0},
                "loser1": {"id": "2", "firstName": "Kira", "lastName": "Elliott", "singlesUtr": 4.5},
            },
            "isWinner": False,
            "date": "2026-09-13T00:00:00",
            "score": {"1": {"winner": 6, "loser": 1}, "2": {"winner": 6, "loser": 1}},
        }]}],
    }]
}
with patch("scripts.utr_scraper.requests.get", return_value=v4_resp):
    th = utr.get_player_match_history("1")
assert th[0]["opponent_utr_id"] == "2"
assert th[0]["score"] == "1-6 1-6", f"Expected owner-perspective score, got {th[0]['score']}"
assert th[0]["win"] is False
print("  USTA + UTR parsing: PASSED")

# These two synthetic matches (same date + score) should correlate
pairs = matcher.pair_from_match_histories(um, th)
assert len(pairs) == 1 and pairs[0]["usta_id"] == "2018579279" and pairs[0]["utr_id"] == "2"
print("  End-to-end correlation of synthetic payloads: PASSED")


# 7. Scratch-DB schema creation (bootstrap --db / prepare_db)
print("\n[7/7] Testing scratch-DB initialization (prepare_db)...")
from scripts.bootstrap import prepare_db

scratch = os.path.join(tempfile.gettempdir(), "tennislink_test_bootstrap_scratch.db")
try:
    os.remove(scratch)
except OSError:
    pass
prepare_db(scratch, reset=True)
with db.get_connection() as conn:
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
for t in ("usta_player_profiles", "utr_player_profiles", "player_mappings"):
    assert t in names, f"Missing table {t} in scratch DB: {sorted(names)}"
assert count_mappings() == 0, "Reset scratch DB should have 0 mappings"
print(f"  Scratch DB schema created ({len(names)} tables): PASSED")


# Cleanup
print("\nCleaning up scratch DBs...")
import sqlite3, time, gc
for path in (db.DB_PATH, os.path.join(tempfile.gettempdir(), "tennislink_test_bootstrap.db")):
    try:
        sqlite3.connect(path).close()
    except Exception:
        pass
    for _ in range(3):
        try:
            os.remove(path)
            break
        except (PermissionError, FileNotFoundError):
            time.sleep(0.5)
            gc.collect()

print("\n" + "=" * 60)
print("Bootstrap: ALL TESTS PASSED")
print("=" * 60)
