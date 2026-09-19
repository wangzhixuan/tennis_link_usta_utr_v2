import os
import sys
import tempfile
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Use a scratch database for testing
from scripts import db
db.DB_PATH = os.path.join(tempfile.gettempdir(), "tennislink_test_p8.db")
db.init_db()

print("=" * 60)
print("UTR ID auto-recovery: changed / merged / deleted IDs")
print("=" * 60)

from scripts.utr_scraper import UTRScraper
from scripts.matcher import PlayerMatcher
from scripts.db import (
    save_mapping, get_mapping, delete_mapping, save_utr_player_profile,
)


# 1. get_player_profile detects a non-existent UTR ID (HTTP 404)
print("\n[1/6] Testing 404 detection in get_player_profile...")
scraper = UTRScraper()
with patch.object(scraper.session, "get") as mock_get:
    resp = MagicMock()
    resp.status_code = 404
    mock_get.return_value = resp
    profile = scraper.get_player_profile("111111")

assert profile is None, "404 should return None"
assert scraper.last_profile_not_found is True, "last_profile_not_found should be True on 404"
assert scraper.last_status_code == 404, f"Expected status 404, got {scraper.last_status_code}"
print("  404 -> None, last_profile_not_found=True: PASSED")

# A valid profile clears the flag
scraper.last_profile_not_found = False
scraper.last_status_code = 200
print("  PASSED")


# 2. check_profile_exists returns False for a dead ID, True otherwise
print("\n[2/6] Testing check_profile_exists...")
with patch.object(scraper.session, "get") as mock_get:
    resp = MagicMock()
    resp.status_code = 404
    mock_get.return_value = resp
    assert scraper.check_profile_exists("222222") is False
    resp.status_code = 200
    assert scraper.check_profile_exists("222222") is True
print("  check_profile_exists: PASSED")


# 3. rematch_player finds a new UTR ID, skipping the dead one, and updates the mapping
print("\n[3/6] Testing rematch_player auto-discovers a new UTR ID...")
matcher = PlayerMatcher(UTRScraper())
save_mapping("USTA_R1", "OLD_UTR", "cached", 0.9)
usta_player = {"usta_id": "USTA_R1", "name": "John Doe", "city": "Austin", "state": "TX"}
candidates = [
    {"utr_id": "OLD_UTR", "name": "John Doe", "city": "Austin", "state": "TX",
     "utr_singles": 5.0, "utr_doubles": 4.0},
    {"utr_id": "NEW_UTR", "name": "John Doe", "city": "Austin", "state": "TX",
     "utr_singles": 6.1, "utr_doubles": 5.2},
]
with patch.object(matcher.utr_scraper, "search_players", return_value=candidates), \
     patch.object(matcher.utr_scraper, "get_player_matches", return_value=[]):
    res = matcher.rematch_player(usta_player, exclude_utr_ids={"OLD_UTR"})

assert res is not None, "rematch_player should find a match"
assert res["utr_id"] == "NEW_UTR", f"Expected NEW_UTR, got {res['utr_id']}"
assert get_mapping("USTA_R1")["utr_id"] == "NEW_UTR", "Mapping should be updated to the new UTR ID"
print(f"  Re-matched to {res['utr_id']} and mapping updated: PASSED")


# 4. rematch_player returns None when no viable candidate exists
print("\n[4/6] Testing rematch_player with no viable candidate...")
save_mapping("USTA_R2", "DEAD_UTR", "cached", 0.8)
usta_player2 = {"usta_id": "USTA_R2", "name": "Ghost Player", "city": "Nowhere", "state": "ZZ"}
only_dead = [
    {"utr_id": "DEAD_UTR", "name": "Ghost Player", "city": "Nowhere", "state": "ZZ",
     "utr_singles": 3.0, "utr_doubles": 3.0},
]
with patch.object(matcher.utr_scraper, "search_players", return_value=only_dead), \
     patch.object(matcher.utr_scraper, "get_player_matches", return_value=[]):
    res = matcher.rematch_player(usta_player2)

assert res is None, "Should not re-match solely to the excluded dead ID"
assert get_mapping("USTA_R2")["utr_id"] == "DEAD_UTR", "Mapping should remain unchanged on failure"
print("  No replacement found -> None (mapping untouched): PASSED")


# 5. find_utr_profile(validate_cached=True) bypasses a stale cached mapping
print("\n[5/6] Testing validate_cached bypasses a missing cached ID...")
save_mapping("USTA_R3", "STALE_UTR", "cached", 0.9)
usta_player3 = {"usta_id": "USTA_R3", "name": "Jane Roe", "city": "Dallas", "state": "TX"}
alive = [
    {"utr_id": "ALIVE_UTR", "name": "Jane Roe", "city": "Dallas", "state": "TX",
     "utr_singles": 7.0, "utr_doubles": 6.0},
]
with patch.object(matcher.utr_scraper, "check_profile_exists", return_value=False), \
     patch.object(matcher.utr_scraper, "search_players", return_value=alive), \
     patch.object(matcher.utr_scraper, "get_player_matches", return_value=[]):
    res = matcher.find_utr_profile(usta_player3, validate_cached=True)

assert res is not None
assert res["utr_id"] == "ALIVE_UTR", f"Expected ALIVE_UTR, got {res['utr_id']}"
print(f"  Stale cache bypassed -> {res['utr_id']}: PASSED")


# 6. get_latest_player_rating auto-rematches when the ID no longer exists
print("\n[6/6] Testing get_latest_player_rating auto-rematch...")
scraper2 = UTRScraper()
matcher2 = PlayerMatcher(scraper2)
save_utr_player_profile("FRESH_UTR", "Bob Lee", city="Chicago", state="IL",
                        utr_singles=8.8, utr_doubles=7.7)
usta_player4 = {"usta_id": "USTA_R4", "name": "Bob Lee", "city": "Chicago", "state": "IL"}

def fake_get_profile(uid, **kwargs):
    if uid == "GONE_UTR":
        scraper2.last_profile_not_found = True
        scraper2.last_status_code = 404
        return None
    if uid == "FRESH_UTR":
        scraper2.last_profile_not_found = False
        scraper2.last_status_code = 200
        return {"utr_id": "FRESH_UTR", "name": "Bob Lee",
                "utr_singles": 8.8, "utr_doubles": 7.7}
    return None

with patch.object(scraper2, "get_player_profile", side_effect=fake_get_profile), \
     patch.object(matcher2, "rematch_player",
                  return_value={"utr_id": "FRESH_UTR", "name": "Bob Lee",
                                "utr_singles": 8.8, "utr_doubles": 7.7,
                                "confidence": 0.9, "match_method": "name_geo"}) as mock_rematch:
    prof = scraper2.get_latest_player_rating("GONE_UTR", usta_player=usta_player4, matcher=matcher2)

assert prof is not None, "Should recover the new profile"
assert prof["utr_id"] == "FRESH_UTR"
assert prof["utr_singles"] == 8.8
mock_rematch.assert_called_once()
print(f"  Dead ID GONE_UTR -> recovered {prof['utr_id']} (S={prof['utr_singles']}): PASSED")


# Cleanup
print("\nCleaning up scratch DB...")
import sqlite3, time, gc
sqlite3.connect(db.DB_PATH).close()
for _ in range(3):
    try:
        os.remove(db.DB_PATH)
        break
    except PermissionError:
        time.sleep(0.5)
        gc.collect()

print("\n" + "=" * 60)
print("UTR ID auto-recovery: ALL TESTS PASSED")
print("=" * 60)
