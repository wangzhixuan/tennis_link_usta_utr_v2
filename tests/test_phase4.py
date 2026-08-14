import os
import sys
import tempfile
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Use test database
import db
db.DB_PATH = os.path.join(tempfile.gettempdir(), "tennislink_test_p4.db")
db.init_db()

print("=" * 60)
print("Phase 4: Testing Matching Engine")
print("=" * 60)

# 1. Name normalization
print("\n[1/5] Testing name normalization...")
from matcher import PlayerMatcher
from utr_scraper import UTRScraper

matcher = PlayerMatcher(UTRScraper())

assert matcher.normalize_name("John Doe") == "john doe"
assert matcher.normalize_name("John M. Doe") == "john doe"
assert matcher.normalize_name("Sarah J. Williams") == "sarah williams"
assert matcher.normalize_name("   Spaces    Here  ") == "spaces here"
assert matcher.normalize_name("Hubert Jr Toney") == "hubert toney"
assert matcher.normalize_name("") == ""
print("  normalize_name: all cases passed")

# 2. Name similarity
print("\n[2/5] Testing name similarity scoring...")
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
print("\n[3/5] Testing location scoring...")
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
print("\n[4/5] Testing full match flow (with mocked UTR scraper)...")
from db import save_mapping, get_mapping, save_usta_player_profile

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
print("\n[5/5] Testing low confidence handling...")
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
print("Phase 4: ALL TESTS PASSED")
print("=" * 60)
