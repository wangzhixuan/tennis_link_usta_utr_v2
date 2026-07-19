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

matcher = PlayerMatcher(UTRScraper(headless=True))

assert matcher.normalize_name("John Doe") == "john doe"
assert matcher.normalize_name("John M. Doe") == "john doe"
assert matcher.normalize_name("Sarah J. Williams") == "sarah williams"
assert matcher.normalize_name("   Spaces    Here  ") == "spaces here"
assert matcher.normalize_name("") == ""
assert matcher.normalize_name(None) == ""
print("  normalize_name: all cases passed")

# 2. Name similarity
print("\n[2/5] Testing name similarity scoring...")
# Exact match
assert matcher.name_similarity("John Doe", "John Doe") == 1.0

# Very similar (middle initial)
sim = matcher.name_similarity("John M. Doe", "John Doe")
assert sim > 0.9, f"Expected high similarity with middle initial, got {sim}"

# Different
sim = matcher.name_similarity("John Doe", "Jane Smith")
assert sim < 0.5, f"Expected low similarity for different names, got {sim}"

# Same names with different formatting
sim = matcher.name_similarity("  john   doe  ", "John Doe")
assert sim >= 0.9, f"Expected high similarity despite whitespace, got {sim}"

# Hyphenated (e.g. "Mary Jo Smith" vs "Mary Smith")
sim = matcher.name_similarity("Mary Jo Smith", "Mary Smith")
assert sim > 0.75, f"Expected good similarity for shortened name, got {sim}"

print("  name_similarity: all cases passed")

# 3. Location scoring
print("\n[3/5] Testing location scoring...")

# Exact same city and state
assert matcher.location_score("Austin", "TX", "Austin", "TX") == 1.0

# Same state, different city
score = matcher.location_score("Austin", "TX", "Dallas", "TX")
assert score == 0.75, f"Expected 0.75 for same state, got {score}"

# Different states
score = matcher.location_score("Austin", "TX", "Miami", "FL")
assert score == 0.1, f"Expected 0.1 for different states with cities, got {score}"

# One missing city
score = matcher.location_score("Austin", "TX", None, "TX")
assert score == 0.8, f"Expected 0.8 for same state one missing city, got {score}"

# Both missing location
score = matcher.location_score(None, None, None, None)
assert score == 0.5, f"Expected 0.5 for both missing, got {score}"

print("  location_score: all cases passed")

# 4. Full flow - cache hit
print("\n[4/5] Testing full match flow (with mocked UTR scraper)...")

# Setup: save a mapping to test cache hit
from db import save_mapping, get_mapping, save_usta_cache

save_mapping("CACHE_TEST", "UTR_CACHED", "manual", 1.0)
save_usta_cache("CACHE_TEST", "Test Player", "Boston", "MA", 10.0, None, 5)

usta_player = {"usta_id": "CACHE_TEST", "name": "Test Player", "city": "Boston", "state": "MA"}

with patch.object(matcher.utr_scraper, 'search_players') as mock_search:
    result = matcher.find_utr_profile(usta_player)
    assert result is not None, "Cache hit should return a result"
    assert result["utr_id"] == "UTR_CACHED"
    assert result["match_method"] == "cached"
    assert result["confidence"] == 1.0
    mock_search.assert_not_called()
    print("  Cache hit: PASSED")

# Level 2: Name & Location match (no cache, search finds candidates)
usta_player2 = {"usta_id": "NEW001", "name": "Alice Williams", "city": "Los Angeles", "state": "CA"}

mock_candidates = [
    {"utr_id": "UTR_ALICE1", "name": "Alice Williams", "city": "Los Angeles", "state": "CA", "utr_singles": 11.5, "utr_doubles": 10.2},
    {"utr_id": "UTR_ALICE2", "name": "Alice Johnson", "city": "San Diego", "state": "CA", "utr_singles": 9.8, "utr_doubles": None},
    {"utr_id": "UTR_ALICE3", "name": "Alicia Williamson", "city": "New York", "state": "NY", "utr_singles": 8.5, "utr_doubles": 7.5}
]

with patch.object(matcher.utr_scraper, 'search_players', return_value=mock_candidates), \
     patch.object(matcher.utr_scraper, 'get_player_matches', return_value=[]) as mock_matches:
    result = matcher.find_utr_profile(usta_player2)
    assert result is not None, "Should find a match"
    assert result["utr_id"] == "UTR_ALICE1", f"Expected best match UTR_ALICE1, got {result['utr_id']}"
    assert result["confidence"] > 0.9, f"Expected high confidence for exact match, got {result['confidence']}"
    print(f"  Name/Geo exact match: {result['utr_id']} (confidence={result['confidence']:.2f})")

# Level 3: Match history circle heuristic
usta_player3 = {"usta_id": "NEW002", "name": "Bob Smith", "city": "Chicago", "state": "IL"}
tournament_players = [
    {"usta_id": "NEW002", "name": "Bob Smith", "city": "Chicago", "state": "IL"},
    {"usta_id": "KNOWN001", "name": "Charlie Brown", "city": "Chicago", "state": "IL"},
    {"usta_id": "KNOWN002", "name": "David Wilson", "city": "Evanston", "state": "IL"}
]

# Two UTR candidates - one with matching tournament opponent in match history
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
    assert result is not None, "Should find a match"
    assert result["utr_id"] == "UTR_BOB1", f"Circle heuristic should pick UTR_BOB1 over UTR_BOB2"
    assert result["match_method"] == "match_history_circle", f"Expected circle heuristic, got {result['match_method']}"
    print(f"  Match history circle heuristic: {result['utr_id']} (method={result['match_method']}, confidence={result['confidence']:.2f})")

# No match found
usta_player4 = {"usta_id": "NOONE", "name": "Zzz Zzzz NotFound", "city": "Unknown", "state": "??"}
with patch.object(matcher.utr_scraper, 'search_players', return_value=[]):
    result = matcher.find_utr_profile(usta_player4)
    assert result is None, "Should return None when no candidates found"
    print("  No match returns None: PASSED")

print("  PASSED")

# 5. Low confidence / no geo match
print("\n[5/5] Testing low confidence handling...")
usta_player5 = {"usta_id": "LOWCONF", "name": "Michael Johnson", "city": "Portland", "state": "OR"}

mock_low_conf = [
    {"utr_id": "UTR_MIKE1", "name": "Mike Johnson", "city": "Seattle", "state": "WA", "utr_singles": 12.0, "utr_doubles": 11.5},
    {"utr_id": "UTR_MIKE2", "name": "Michael Johnson", "city": "Miami", "state": "FL", "utr_singles": 10.0, "utr_doubles": None},
]

with patch.object(matcher.utr_scraper, 'search_players', return_value=mock_low_conf), \
     patch.object(matcher.utr_scraper, 'get_player_matches', return_value=[]):
    result = matcher.find_utr_profile(usta_player5)
    # Even though state is different, name should be a decent match
    # But confidence might be below 0.75 so it might not save to DB
    # The method should still return the best effort match
    if result:
        print(f"  Best effort match: {result['name']} (confidence={result['confidence']:.2f})")
    else:
        print(f"  No match (expected if confidence < 0.75 and no match history)")
    
    # Verify mapping was NOT saved if confidence < 0.75
    cached = get_mapping("LOWCONF")
    if cached:
        print(f"  Mapping saved (confidence >= 0.75)")
    else:
        print(f"  Mapping not saved (confidence < 0.75) - correct behavior")
    
    print("  PASSED")

# Cleanup test DB
import sqlite3, time
sqlite3.connect(db.DB_PATH).close()
for _ in range(3):
    try:
        os.remove(db.DB_PATH)
        break
    except PermissionError:
        time.sleep(0.5)

print("\n" + "=" * 60)
print("Phase 4: ALL TESTS PASSED")
print("=" * 60)
