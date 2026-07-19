import os
import sys
import json
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

print("=" * 60)
print("Phase 3: Testing UTR Scraper")
print("=" * 60)

# 1. Class instantiation
print("\n[1/5] Testing class instantiation...")
from utr_scraper import UTRScraper
scraper = UTRScraper(email="test@test.com", password="testpass", headless=True)
assert scraper.email == "test@test.com"
assert scraper.headless == True
print("  PASSED")

# 2. Test search_players with mocked API response
print("\n[2/5] Testing search_players API response parsing...")
mock_search_response = {
    "results": [
        {
            "source": {
                "id": "UTR12345",
                "name": "Alice Williams",
                "location": {"city": "Los Angeles", "state": "CA"},
                "singlesUtr": 11.2,
                "doublesUtr": 10.5
            }
        },
        {
            "source": {
                "id": "UTR67890",
                "name": "Alice Johnson",
                "location": {"city": "San Francisco", "state": "CA"},
                "singlesUtr": 9.8
            }
        },
        {
            "id": "UTR11111",
            "name": "Alice Brown",
            "city": "New York",
            "state": "NY",
            "singlesUtr": 8.5,
            "doublesUtr": 7.9
        }
    ]
}

with patch.object(scraper.session, 'get') as mock_get:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = mock_search_response
    mock_get.return_value = resp
    
    candidates = scraper.search_players("Alice")
    
    print(f"  Found {len(candidates)} candidates from API response")
    for c in candidates:
        print(f"    UTR ID: {c['utr_id']}, Name: {c['name']}, Location: {c.get('city')},{c.get('state')}, UTR S: {c.get('utr_singles')}, UTR D: {c.get('utr_doubles')}")
    
    assert len(candidates) == 3, f"Expected 3 candidates, got {len(candidates)}"

assert candidates[0]["utr_id"] == "UTR12345"
assert candidates[0]["name"] == "Alice Williams"
assert candidates[0]["city"] == "Los Angeles"
assert candidates[0]["state"] == "CA"
assert candidates[0]["utr_singles"] == 11.2
assert candidates[0]["utr_doubles"] == 10.5
print("  PASSED")

# 3. Test get_player_matches parsing
print("\n[3/5] Testing get_player_matches API response parsing...")
mock_matches_response = {
    "results": [
        {
            "opponent": {"name": "Bob Smith"},
            "date": "2024-06-15",
            "score": "6-4, 6-3",
            "outcome": "WIN"
        },
        {
            "opponent": {"name": "Carol Davis"},
            "date": "2024-05-20",
            "score": "7-5, 4-6, 6-2",
            "outcome": "LOSS",
            "win": False
        }
    ]
}

with patch.object(scraper.session, 'get') as mock_get:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = mock_matches_response
    mock_get.return_value = resp
    
    matches = scraper.get_player_matches("UTR12345")
    
    print(f"  Retrieved {len(matches)} matches")
    assert len(matches) == 2
    assert matches[0]["opponent_name"] == "Bob Smith"
    assert matches[0]["win"] == True
    assert matches[1]["opponent_name"] == "Carol Davis"
    assert matches[1]["win"] == False
    print(f"  Match 1: {matches[0]['opponent_name']} - {matches[0]['date']} - Win={matches[0]['win']}")
    print(f"  Match 2: {matches[1]['opponent_name']} - {matches[1]['date']} - Win={matches[1]['win']}")
    print("  PASSED")

# 4. Test search_players with empty/error responses
print("\n[4/5] Testing search_players error handling...")

# Patch both API and fallback to avoid browser launch in tests
with patch.object(scraper.session, 'get') as mock_get, \
     patch.object(scraper, '_fallback_playwright_search', return_value=[]) as mock_fallback:
    
    resp = MagicMock()
    resp.status_code = 403
    mock_get.return_value = resp
    
    candidates = scraper.search_players("Nonexistent Player")
    assert candidates == [], f"Expected empty list on API failure, got {candidates}"
    assert mock_fallback.called, "Should have attempted fallback"
    print("  API failure + empty fallback returns empty list: PASSED")

with patch.object(scraper.session, 'get') as mock_get, \
     patch.object(scraper, '_fallback_playwright_search', return_value=[]) as mock_fallback:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"status": "error", "message": "not found"}
    mock_get.return_value = resp
    
    candidates = scraper.search_players("Unknown")
    assert candidates == [], f"Expected empty list for unexpected format, got {candidates}"
    assert mock_fallback.called, "Should have attempted fallback"
    print("  Unexpected JSON format returns empty list: PASSED")
    print("  PASSED")

# 5. Test login detection (without actually running Playwright)
print("\n[5/5] Testing login guard behavior...")
# Without credentials
scraper_no_creds = UTRScraper(email="", password="")
result = scraper_no_creds.login_and_capture_session()
assert result == False, "Should return False when no credentials provided"
print("  Empty credentials returns False: PASSED")
print("  PASSED")

print("\n" + "=" * 60)
print("Phase 3: ALL TESTS PASSED")
print("=" * 60)
