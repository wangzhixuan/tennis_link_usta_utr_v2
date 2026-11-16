import os
import sys
import tempfile
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Use test database
from scripts.service import db
db.DB_PATH = os.path.join(tempfile.gettempdir(), "tennislink_test_p4.db")
db.init_db()
db.reset_all()  # start clean even if a previous run left data behind

print("=" * 60)
print("Player Matcher: name/location + match-history")
print("=" * 60)

# 1. Name normalization
print("\n[1/7] Testing name normalization...")
from scripts.service.matcher import PlayerMatcher
from scripts.service.utr_scraper import UTRScraper

matcher = PlayerMatcher(UTRScraper())

assert matcher.normalize_name("John Doe") == "john doe"
assert matcher.normalize_name("John M. Doe") == "john doe"
assert matcher.normalize_name("Sarah J. Williams") == "sarah williams"
assert matcher.normalize_name("   Spaces    Here  ") == "spaces here"
assert matcher.normalize_name("Hubert Jr Toney") == "hubert toney"
assert matcher.normalize_name("") == ""
print("  normalize_name: all cases passed")

# 2. Name similarity
print("\n[2/7] Testing name similarity scoring...")
assert matcher.name_similarity("John Doe", "John Doe") == 1.0

sim = matcher.name_similarity("John M. Doe", "John Doe")
assert sim > 0.9, f"Expected high sim with middle initial, got {sim}"

sim = matcher.name_similarity("John Doe", "Jane Smith")
assert sim < 0.5, f"Expected low sim for different names, got {sim}"

sim = matcher.name_similarity("  john   doe  ", "John Doe")
assert sim >= 0.9, f"Expected high sim despite whitespace, got {sim}"

sim = matcher.name_similarity("Mary Jo Smith", "Mary Smith")
assert sim > 0.75, f"Expected good sim for shortened name, got {sim}"

sim = matcher.name_similarity("Hubert Jr Toney", "Hubert Toney")
assert sim > 0.9, f"Expected high sim for Jr suffix removal, got {sim}"
print("  name_similarity: all cases passed")

# 3. Location scoring (with state abbreviation mapping)
print("\n[3/7] Testing location scoring...")
assert matcher.location_score("Austin", "TX", "Austin", "TX") == 1.0
assert matcher.location_score("Austin", "TX", "Austin", "Texas") == 1.0
assert matcher.location_score("Austin", "Texas", "Austin", "TX") == 1.0
assert matcher.location_score("Austin", "TX", "Dallas", "TX") == 0.75
assert matcher.location_score("Austin", "TX", "Miami", "FL") == 0.1
assert matcher.location_score("Austin", "TX", None, "TX") == 0.8
assert matcher.location_score(None, None, None, None) == 0.5
assert matcher.location_score("Orlando", "FL", "Orlando", "Florida") == 1.0
print("  location_score: all cases passed")

# 4. Full match flow - cache hit
print("\n[4/7] Testing full match flow (with mocked UTR scraper)...")
from scripts.service.db import save_mapping, get_mapping, save_usta_player_profile

save_mapping("CACHE_TEST", "UTR_CACHED", "manual", 1.0)
save_usta_player_profile("CACHE_TEST", "Test Player", city="Boston", state="MA", wtn_singles=10.0, rankings=[{"points": 5}])

usta_player = {"usta_id": "CACHE_TEST", "name": "Test Player", "city": "Boston", "state": "MA"}

with patch.object(matcher.utr_scraper, 'search_players') as mock_search:
    result = matcher.find_utr_profile(usta_player)
    assert result is not None
    assert result["utr_id"] == "UTR_CACHED"
    assert result["match_method"] == "cached"
    assert result["confidence"] == 1.0
    mock_search.assert_not_called()
    print("  Cache hit: PASSED")

# Level 2: Name & Location match (use fresh ID not in cache)
usta_player2 = {"usta_id": "FRESH_ALICE", "name": "Alice Williams", "city": "Los Angeles", "state": "California"}

mock_candidates = [
    {"utr_id": "UTR_ALICE1", "name": "Alice Williams", "city": "Los Angeles", "state": "California", "utr_singles": 11.5, "utr_doubles": 10.2},
    {"utr_id": "UTR_ALICE2", "name": "Alice Johnson", "city": "San Diego", "state": "CA", "utr_singles": 9.8, "utr_doubles": None},
    {"utr_id": "UTR_ALICE3", "name": "Alicia Williamson", "city": "New York", "state": "NY", "utr_singles": 8.5, "utr_doubles": 7.5}
]

with patch.object(matcher.utr_scraper, 'search_players', return_value=mock_candidates), \
     patch.object(matcher.utr_scraper, 'get_player_matches', return_value=[]) as mock_matches:
    result = matcher.find_utr_profile(usta_player2)
    assert result is not None
    assert result["utr_id"] == "UTR_ALICE1", f"Expected UTR_ALICE1, got {result['utr_id']}"
    assert result["confidence"] > 0.9
    print(f"  Name/Geo exact match: {result['utr_id']} (confidence={result['confidence']:.2f})")

# Level 3: Match history circle heuristic (use fresh IDs not in cache)
usta_player3 = {"usta_id": "FRESH_001", "name": "Bob Smith", "city": "Chicago", "state": "IL"}
tournament_players = [
    {"usta_id": "FRESH_001", "name": "Bob Smith", "city": "Chicago", "state": "IL"},
    {"usta_id": "FRESH_002", "name": "Charlie Brown", "city": "Chicago", "state": "IL"},
    {"usta_id": "FRESH_003", "name": "David Wilson", "city": "Evanston", "state": "IL"}
]

mock_candidates_bob = [
    {"utr_id": "UTR_BOB1", "name": "Bob Smith", "city": "Chicago", "state": "IL", "utr_singles": 7.5, "utr_doubles": 6.8},
    {"utr_id": "UTR_BOB2", "name": "Robert Smith", "city": "Chicago", "state": "IL", "utr_singles": 8.0, "utr_doubles": 7.5}
]

def get_player_matches_side_effect(utr_id):
    if utr_id == "UTR_BOB1":
        return [{"opponent_name": "Charlie Brown", "date": "2024-03-10", "score": "6-2 6-3", "win": True}]
    return []

with patch.object(matcher.utr_scraper, 'search_players', return_value=mock_candidates_bob), \
     patch.object(matcher.utr_scraper, 'get_player_matches', side_effect=get_player_matches_side_effect):
    result = matcher.find_utr_profile(usta_player3, tournament_players)
    assert result is not None
    assert result["utr_id"] == "UTR_BOB1", "Circle heuristic should pick UTR_BOB1"
    assert result["match_method"] == "match_history_circle"
    print(f"  Match history circle: {result['utr_id']} (confidence={result['confidence']:.2f})")

# No match found
usta_player4 = {"usta_id": "NOONE", "name": "Zzz Zzzz NotFound", "city": "Unknown", "state": "??"}
with patch.object(matcher.utr_scraper, 'search_players', return_value=[]):
    result = matcher.find_utr_profile(usta_player4)
    assert result is None
    print("  No match returns None: PASSED")
print("  PASSED")

# 5. Low confidence handling
print("\n[5/7] Testing low confidence handling...")
usta_player5 = {"usta_id": "FRESH_LOWCONF", "name": "Michael Johnson", "city": "Portland", "state": "OR"}

mock_low_conf = [
    {"utr_id": "UTR_MIKE1", "name": "Mike Johnson", "city": "Seattle", "state": "WA", "utr_singles": 12.0, "utr_doubles": 11.5},
    {"utr_id": "UTR_MIKE2", "name": "Michael Johnson", "city": "Miami", "state": "FL", "utr_singles": 10.0, "utr_doubles": None},
]

with patch.object(matcher.utr_scraper, 'search_players', return_value=mock_low_conf), \
     patch.object(matcher.utr_scraper, 'get_player_matches', return_value=[]):
    result = matcher.find_utr_profile(usta_player5)
    if result:
        print(f"  Best effort match: {result['name']} (confidence={result['confidence']:.2f})")
    else:
        print(f"  No match returned")
    
    cached = get_mapping("FRESH_LOWCONF")
    if cached:
        print(f"  Mapping saved (confidence >= 0.75)")
    else:
        print(f"  Mapping not saved (< 0.75)")
    print("  PASSED")

# 6. WTN <-> UTR rating plausibility
print("\n[6/7] Testing WTN<->UTR rating compatibility...")
# WTN 30 -> expected UTR ~4.58
assert matcher.rating_compatibility(30, 4.6) > 0.9
# The real-world bad pairings: a low UTR for a weak WTN, or a high UTR for a very weak WTN
assert matcher.rating_compatibility(25, 1.0) < 0.05
assert matcher.rating_compatibility(34, 6.5) < 0.05
# Missing/zero ratings -> neutral (skipped), not rejected
assert matcher.rating_compatibility(None, 5.0) is None
assert matcher.rating_compatibility(30, 0.0) is None

usta_rating = {"usta_id": "RATING_TEST", "name": "Rating Test", "city": "Austin",
               "state": "TX", "wtn_singles": 34.0}
# Two similar candidates: the plausible WTN/UTR should win the tie-break
good = {"utr_id": "UTR_GOOD", "name": "Rating Test", "city": "Austin", "state": "TX",
        "utr_singles": 3.4}
bad = {"utr_id": "UTR_BAD", "name": "Rating Test", "city": "Dallas", "state": "TX",
       "utr_singles": 6.5}
with patch.object(matcher.utr_scraper, 'search_players', return_value=[bad, good]), \
     patch.object(matcher.utr_scraper, 'get_player_matches', return_value=[]):
    res = matcher.find_utr_profile(usta_rating)
assert res is not None and res["utr_id"] == "UTR_GOOD", "Plausible pair should win the tie-break"
print("  Rating compatibility breaks the tie: PASSED")

# Compatibility must NOT hard-reject: the only (implausible) candidate is still returned
with patch.object(matcher.utr_scraper, 'search_players', return_value=[bad]), \
     patch.object(matcher.utr_scraper, 'get_player_matches', return_value=[]):
    res = matcher.find_utr_profile(usta_rating, ignore_cache=True)
assert res is not None and res["utr_id"] == "UTR_BAD", "Implausible-only must not be hard-rejected"
print("  Implausible-only candidate not hard-rejected: PASSED")

# Real case: exact name+city (WTN 26.8 / UTR 3.51) is accepted
annabelle = {"usta_id": "ANNABELLE", "name": "Annabelle Gramm", "city": "San Diego",
             "state": "CA", "wtn_singles": 26.8}
annabelle_cand = [{"utr_id": "4925193", "name": "Annabelle Gramm", "city": "San Diego",
                   "state": "CA", "utr_singles": 3.51}]
with patch.object(matcher.utr_scraper, 'search_players', return_value=annabelle_cand), \
     patch.object(matcher.utr_scraper, 'get_player_matches', return_value=[]):
    res = matcher.find_utr_profile(annabelle)
assert res is not None and res["utr_id"] == "4925193", "Exact identity pair should be accepted"
print("  Exact identity pair accepted: PASSED")

good_only_player = {"usta_id": "RATING_GOOD_ONLY", "name": "Rating Test",
                    "city": "Austin", "state": "TX", "wtn_singles": 34.0}
good = [{"utr_id": "UTR_GOOD", "name": "Rating Test", "city": "Austin", "state": "TX",
         "utr_singles": 3.4}]
with patch.object(matcher.utr_scraper, 'search_players', return_value=good), \
     patch.object(matcher.utr_scraper, 'get_player_matches', return_value=[]):
    res = matcher.find_utr_profile(good_only_player, ignore_cache=True)
assert res is not None and res["utr_id"] == "UTR_GOOD"
print("  Plausible WTN/UTR pair accepted: PASSED")
print("  PASSED")

# 7. Cached mapping validation (0 rating / implausible WTN-UTR / manual override)
print("\n[7/7] Testing cached mapping validation...")
from scripts.service.db import save_mapping, get_mapping, save_utr_player_profile

# Cached mapping to a 0-rating profile must be invalidated and dropped
save_mapping("CACHE_ZERO", "UTR_ZERO", "golden", 1.0)
save_utr_player_profile("UTR_ZERO", "Zhenghao Wu", city="Irvine", state="CA",
                        utr_singles=0.0, utr_doubles=None)
zero_player = {"usta_id": "CACHE_ZERO", "name": "Kelly Zheng", "city": "San Diego",
               "state": "CA", "wtn_singles": 22.9}
with patch.object(matcher.utr_scraper, 'search_players', return_value=[]), \
     patch.object(matcher.utr_scraper, 'get_player_matches', return_value=[]):
    res = matcher.find_utr_profile(zero_player)
assert res is None, "0-rating cached mapping should be ignored"
assert get_mapping("CACHE_ZERO") is None, "Invalid cached mapping should be deleted"
print("  0-rating cache invalidated: PASSED")

# Cached profile for a different person (even if rated) is invalidated by name
save_mapping("CACHE_BAD", "UTR_BAD_CACHE", "golden", 1.0)
save_utr_player_profile("UTR_BAD_CACHE", "Thomas Carlisle", city="Rancho Palos Verdes",
                        state="CA", utr_singles=1.42, utr_doubles=1.0)
bad_player = {"usta_id": "CACHE_BAD", "name": "Auristella Carlisle",
              "city": "Rancho Palos Verdes", "state": "CA", "wtn_singles": 23.53}
with patch.object(matcher.utr_scraper, 'search_players', return_value=[]), \
     patch.object(matcher.utr_scraper, 'get_player_matches', return_value=[]):
    res = matcher.find_utr_profile(bad_player)
assert res is None
assert get_mapping("CACHE_BAD") is None
print("  Wrong-person cache invalidated: PASSED")

# Same-name cached mapping is kept even with an implausible rating (no hard reject)
save_mapping("CACHE_DUP", "UTR_DUP", "golden", 1.0)
save_utr_player_profile("UTR_DUP", "Kelly Zheng", city="Irvine", state="CA",
                        utr_singles=6.5, utr_doubles=None)
dup_player = {"usta_id": "CACHE_DUP", "name": "Kelly Zheng", "city": "San Diego",
              "state": "CA", "wtn_singles": 34.0}
with patch.object(matcher.utr_scraper, 'search_players', return_value=[]) as ms:
    res = matcher.find_utr_profile(dup_player)
assert res is not None and res["utr_id"] == "UTR_DUP"
ms.assert_not_called()
print("  Same-name cache kept despite rating gap: PASSED")

# Exact name + same city/state is kept even when the rating is a poor fit (Annabelle)
save_mapping("CACHE_STRONG", "UTR_STRONG", "golden", 1.0)
save_utr_player_profile("UTR_STRONG", "Annabelle Gramm", city="San Diego", state="CA",
                        utr_singles=3.51, utr_doubles=3.17)
strong_player = {"usta_id": "CACHE_STRONG", "name": "Annabelle Gramm", "city": "San Diego",
                 "state": "CA", "wtn_singles": 26.8}
with patch.object(matcher.utr_scraper, 'search_players', return_value=[]) as ms:
    res = matcher.find_utr_profile(strong_player)
assert res is not None and res["utr_id"] == "UTR_STRONG"
ms.assert_not_called()
print("  Exact-identity cache kept despite rating gap: PASSED")

# A compatible cached mapping is still trusted (no search performed)
save_mapping("CACHE_OK", "UTR_OK_CACHE", "golden", 1.0)
save_utr_player_profile("UTR_OK_CACHE", "Kelly Zheng", city="San Diego", state="CA",
                        utr_singles=4.8, utr_doubles=None)
ok_player = {"usta_id": "CACHE_OK", "name": "Kelly Zheng", "city": "San Diego",
             "state": "CA", "wtn_singles": 30.0}
with patch.object(matcher.utr_scraper, 'search_players', return_value=[]) as ms:
    res = matcher.find_utr_profile(ok_player)
assert res is not None and res["utr_id"] == "UTR_OK_CACHE"
ms.assert_not_called()
print("  Compatible cache trusted: PASSED")

# A manual correction is always trusted, even with a 0 rating
save_mapping("CACHE_MANUAL", "UTR_MANUAL", "manual_correct", 1.0)
save_utr_player_profile("UTR_MANUAL", "Someone", utr_singles=0.0, utr_doubles=None)
manual_player = {"usta_id": "CACHE_MANUAL", "name": "Kelly Zheng", "city": "San Diego",
                 "state": "CA", "wtn_singles": 22.9}
with patch.object(matcher.utr_scraper, 'search_players', return_value=[]):
    res = matcher.find_utr_profile(manual_player)
assert res is not None and res["utr_id"] == "UTR_MANUAL"
print("  Manual correction trusted: PASSED")

# cache_only mode (used when UTR is rate-limited): cache hit without network,
# and no-cache returns None without searching.
save_mapping("CACHE_ONLY", "UTR_ONLY", "golden", 1.0)
save_utr_player_profile("UTR_ONLY", "Only Player", city="San Diego", state="CA",
                        utr_singles=5.0, utr_doubles=4.0)
only_player = {"usta_id": "CACHE_ONLY", "name": "Only Player", "city": "San Diego",
               "state": "CA", "wtn_singles": 30.0}
with patch.object(matcher.utr_scraper, 'search_players') as ms:
    res = matcher.find_utr_profile(only_player, cache_only=True)
assert res is not None and res["utr_id"] == "UTR_ONLY"
ms.assert_not_called()

no_cache_player = {"usta_id": "NO_CACHE_ONLY", "name": "Nobody Here",
                   "city": "San Diego", "state": "CA", "wtn_singles": 30.0}
with patch.object(matcher.utr_scraper, 'search_players') as ms:
    res = matcher.find_utr_profile(no_cache_player, cache_only=True)
assert res is None
ms.assert_not_called()
print("  cache_only uses cache / skips network: PASSED")
print("  PASSED")

# Cleanup test DB
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
print("Player Matcher: ALL TESTS PASSED")
print("=" * 60)
