import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

print("=" * 60)
print("Phase 3: Testing UTR Scraper")
print("=" * 60)

# 1. Class instantiation
print("\n[1/5] Testing class instantiation...")
from scripts.utr_scraper import UTRScraper
scraper = UTRScraper(email="test@test.com", password="testpass")
assert scraper.email == "test@test.com"
assert scraper.password == "testpass"
print("  PASSED")

# 2. Test search_players with real Elasticsearch response format
print("\n[2/5] Testing search_players API response parsing...")
mock_search_response = {
    "hits": [
        {
            "id": "UTR_ALICE1",
            "source": {
                "id": 12345,
                "displayName": "Alice Williams",
                "firstName": "Alice",
                "lastName": "Williams",
                "location": {"cityName": "Los Angeles", "stateName": "California", "display": "Los Angeles, CA"},
                "singlesUtr": 11.2,
                "doublesUtr": 10.5
            }
        },
        {
            "id": "UTR_ALICE2",
            "source": {
                "id": 67890,
                "displayName": "Alice Johnson",
                "firstName": "Alice",
                "lastName": "Johnson",
                "location": {"cityName": "San Francisco", "stateName": "California"},
                "singlesUtr": 9.8
            }
        },
        {
            "id": "UTR_ALICE3",
            "source": {
                "id": 11111,
                "displayName": "Alice Brown",
                "firstName": "Alice",
                "lastName": "Brown",
                "singlesUtr": 8.5,
                "doublesUtr": 7.9
            }
        }
    ],
    "total": 3
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

assert candidates[0]["utr_id"] == "12345"
assert candidates[0]["name"] == "Alice Williams"
assert candidates[0]["city"] == "Los Angeles"
assert candidates[0]["state"] == "California"
assert candidates[0]["utr_singles"] == 11.2
assert candidates[0]["utr_doubles"] == 10.5

assert candidates[2]["city"] is None  # No location field
assert candidates[2]["utr_singles"] == 8.5
print("  PASSED")

# 3. Test get_player_matches parsing
print("\n[3/5] Testing get_player_matches API response...")
mock_matches_response = {
    "results": [
        {"opponent": {"name": "Bob Smith"}, "date": "2024-06-15", "score": "6-4, 6-3", "outcome": "WIN"},
        {"opponent": {"name": "Carol Davis"}, "date": "2024-05-20", "score": "7-5, 4-6, 6-2", "outcome": "LOSS"}
    ]
}

# Need to patch login to return True
with patch.object(scraper, 'login_if_needed', return_value=True), \
     patch.object(scraper.session, 'get') as mock_get:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = mock_matches_response
    mock_get.return_value = resp
    
    matches = scraper.get_player_matches("UTR12345")
    
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

with patch.object(scraper.session, 'get') as mock_get:
    resp = MagicMock()
    resp.status_code = 500
    mock_get.return_value = resp
    candidates = scraper.search_players("Nonexistent Player")
    assert candidates == [], f"Expected empty list, got {len(candidates)}"
    print("  API error returns empty list: PASSED")

with patch.object(scraper.session, 'get') as mock_get:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"total": 0, "hits": []}
    mock_get.return_value = resp
    candidates = scraper.search_players("Nobody Here")
    assert candidates == [], f"Expected empty list, got {len(candidates)}"
    print("  Empty hits returns empty list: PASSED")
    print("  PASSED")

# 5. Test login guard behavior
print("\n[5/5] Testing login guard behavior...")
# Without credentials
scraper_no_creds = UTRScraper(email="", password="")
result = scraper_no_creds.login_if_needed()
assert result == False, "Should return False when no credentials"
print("  Empty credentials returns False: PASSED")

# With credentials but login endpoint mocked
scraper_with_creds = UTRScraper(email="real@test.com", password="realpass")
with patch.object(scraper_with_creds.session, 'post') as mock_post:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"accessToken": "test_token_123"}
    mock_post.return_value = resp
    
    result = scraper_with_creds.login_if_needed()
    assert result == True
    assert scraper_with_creds.auth_token == "test_token_123"
    print("  Successful login returns True: PASSED")
    print("  PASSED")

print("\n" + "=" * 60)
print("Phase 3: ALL TESTS PASSED")
print("=" * 60)
