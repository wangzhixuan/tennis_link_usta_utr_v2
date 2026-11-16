import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

print("=" * 60)
print("USTA Tournament Scraper: GUID parsing, draws, filtering")
print("=" * 60)

# 1. Class instantiation and GUID extraction
print("\n[1/5] Testing GUID extraction...")
from scripts.service.usta_scraper import USTAScraper
scraper = USTAScraper()

guid = scraper.extract_guid("D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B")
assert guid == "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B", f"Expected guid, got {guid}"
print(f"  Direct GUID: {guid}")

guid2 = scraper.extract_guid("https://playtennis.usta.com/Competitions/starislandresortkissimmee/Tournaments/players/d6f0b896-6620-4052-b6c4-3abbae1c5a8b")
assert guid2 == "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B"
print(f"  URL-extracted GUID: {guid2}")

try:
    scraper.extract_guid("invalid")
    assert False, "Should raise ValueError"
except ValueError:
    print("  Invalid input correctly raises ValueError")

print("  PASSED")

# 2. Test parse_players with real data
print("\n[2/5] Testing player parsing from API data...")
data_path = os.path.join(os.path.dirname(__file__), "..", "usta_graphql_participants.json")
with open(data_path) as f:
    data = json.load(f)

participants = data.get("data", {}).get("getTournamentParticipants", [])
print(f"  Loaded {len(participants)} participants from saved API response")

players = scraper.parse_players(participants)
print(f"  Parsed {len(players)} players")
assert len(players) > 0, "Should parse at least one player"

# Check first player structure
p = players[0]
assert "usta_id" in p, "Should have usta_id"
assert "name" in p, "Should have name"
assert "city" in p or "state" in p, "Should have location"
print(f"  Sample: {p['name']:25s} | {p.get('city',''):15s} {p.get('state',''):2s} | ID: {p['usta_id']}")
print("  PASSED")

# 3. Test division filtering
print("\n[3/5] Testing division/event filtering...")
from scripts.service.usta_scraper import USTAScraper
events_path = os.path.join(os.path.dirname(__file__), "..", "usta_tournament_full.json")
if os.path.exists(events_path):
    with open(events_path) as f:
        tmt_data = json.load(f)
    events = tmt_data.get("data", {}).get("publishedTournament", {}).get("publishedEvents", [])
    print(f"  Loaded {len(events)} events")

    # Test with gender-based filter (Boys events)
    matched_ids = scraper.build_event_filter(events, "Boys")
    assert matched_ids is not None and len(matched_ids) > 0
    print(f"  'Boys' matched {len(matched_ids)} events")

    all_ids = scraper.build_event_filter(events, None)
    assert all_ids is None, "No filter should return None"
    print("  No filter returns None correctly")

    filtered = scraper.parse_players(participants, matched_ids)
    print(f"  'Boys' filter -> {len(filtered)} players (of {len(players)} total)")
    assert len(filtered) <= len(players), "Filtered set should be <= total"
else:
    print("  Tournament events file not found, skipping")

print("  PASSED")

# 4. Test real API call (if Tournament ID env var set)
print("\n[4/5] Testing real API call (optional)...")
test_guid = os.environ.get("TEST_TOURNAMENT_GUID", "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B")
try:
    real_players = scraper.scrape_tournament(test_guid)
    print(f"  Fetched {len(real_players)} players from real API")
    assert len(real_players) > 0
    print("  PASSED")
except Exception as e:
    print(f"  Skipped (API call failed: {e})")

print("\n[5/5] Testing entry status mapping...")
assert USTAScraper._derive_status([{"entryStatus": "DIRECT_ACCEPTANCE"}]) == "Accepted"
assert USTAScraper._derive_status([{"entryStatus": "ALTERNATE"}]) == "Alternate"
assert USTAScraper._derive_status([{"entryStatus": "WITHDRAWN"}]) == "Withdrawn"
assert USTAScraper._derive_status(
    [{"entryStatus": "WITHDRAWN"}, {"entryStatus": "DIRECT_ACCEPTANCE"}]) == "Accepted"
assert USTAScraper._derive_status([{"entryStage": "MAIN"}]) == "Accepted"
assert USTAScraper._derive_status([]) is None

synthetic = [
    {
        "participantName": "Accepted Player",
        "person": {"addresses": [{"city": "A", "state": "CA"}],
                   "personOtherIds": [{"personId": "1", "uniqueOrganisationName": "USTA"}]},
        "events": [{"eventId": "E1", "entryStatus": "DIRECT_ACCEPTANCE"}],
    },
    {
        "participantName": "Alt Player",
        "person": {"addresses": [{"city": "B", "state": "CA"}],
                   "personOtherIds": [{"personId": "2", "uniqueOrganisationName": "USTA"}]},
        "events": [{"eventId": "E1", "entryStatus": "ALTERNATE"}],
    },
    {
        "participantName": "Withdrawn Player",
        "person": {"addresses": [{"city": "C", "state": "CA"}],
                   "personOtherIds": [{"personId": "3", "uniqueOrganisationName": "USTA"}]},
        "events": [{"eventId": "E1", "entryStatus": "WITHDRAWN"}],
    },
    {
        "participantName": "Other Event",
        "person": {"addresses": [{"city": "D", "state": "CA"}],
                   "personOtherIds": [{"personId": "4", "uniqueOrganisationName": "USTA"}]},
        "events": [{"eventId": "E2", "entryStatus": "DIRECT_ACCEPTANCE"}],
    },
]
parsed = {p["usta_id"]: p for p in USTAScraper.parse_players(synthetic, {"E1"})}
assert parsed["1"]["status"] == "Accepted"
assert parsed["2"]["status"] == "Alternate"
assert parsed["3"]["status"] == "Withdrawn"
assert "4" not in parsed, "Player from another event should be filtered out"
print("  PASSED")

print("\n" + "=" * 60)
print("USTA Tournament Scraper: ALL TESTS PASSED")
print("=" * 60)
