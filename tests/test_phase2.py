import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

print("=" * 60)
print("Phase 2: Testing USTA Scraper")
print("=" * 60)

# 1. Class instantiation
print("\n[1/4] Testing class instantiation...")
from usta_scraper import USTAScraper
scraper = USTAScraper(headless=True)
assert scraper.headless == True
print("  PASSED")

# 2. Test HTML parsing with mock tournament page
print("\n[2/4] Testing HTML player parsing...")
mock_html = """
<html>
<body>
  <div class="player-list">
    <div class="player-row">
      <a href="/players/123456">James Smith</a>
      <span class="location">Austin, TX</span>
      <span class="rating">WTN: 14.2</span>
      <span class="rank">Rank: 25</span>
    </div>
    <div class="player-row">
      <a href="/players/789012">Emily Johnson</a>
      <span class="location">Miami, FL</span>
      <span class="rating">WTN: 18.5</span>
      <span class="rank">Rank: 57</span>
    </div>
    <div class="player-row">
      <a href="/players/345678">Michael Brown</a>
      <span class="location">Chicago, IL</span>
      <span class="rating">WTN: 12.3</span>
      <span class="rank">Rank: 33</span>
    </div>
  </div>
</body>
</html>
"""
players = scraper._parse_players_html(mock_html)
print(f"  Parsed {len(players)} players from HTML")
assert len(players) == 3, "Should find 3 players"

# Verify first player
james = players.get("123456")
assert james is not None, "Should find James Smith"
assert james["name"] == "James Smith"
assert james["city"] == "Austin"
assert james["state"] == "TX"
assert james["wtn_singles"] == 14.2
assert james["ranking"] == 25
print(f"  James Smith: USTA ID={james['usta_id']}, Location={james['city']},{james['state']}, WTN={james['wtn_singles']}, Rank={james['ranking']}")

# Verify second player
emily = players.get("789012")
assert emily is not None
assert emily["name"] == "Emily Johnson"
assert emily["state"] == "FL"
assert emily["wtn_singles"] == 18.5
print(f"  Emily Johnson: USTA ID={emily['usta_id']}, WTN={emily['wtn_singles']}, Rank={emily['ranking']}")

# Verify third player
michael = players.get("345678")
assert michael is not None
assert michael["city"] == "Chicago"
assert michael["state"] == "IL"
print(f"  Michael Brown: USTA ID={michael['usta_id']}, Location={michael['city']},{michael['state']}")
print("  PASSED")

# 3. Test JSON API response parsing
print("\n[3/4] Testing JSON API response parsing...")
mock_json_data = [
    ("/api/tournament/123/players", {
        "players": [
            {
                "ustaId": "111111",
                "fullName": "Sarah Williams",
                "city": "Denver",
                "state": "CO",
                "wtnSingles": 16.8,
                "wtnDoubles": 15.2,
                "rank": 12
            },
            {
                "ustaId": "222222",
                "fullName": "David Lee",
                "city": "Seattle",
                "state": "WA",
                "wtnSingles": 10.5,
                "rank": 88
            }
        ]
    })
]

api_players = scraper._parse_from_captured_json(mock_json_data, None)
print(f"  Parsed {len(api_players)} players from JSON API")
assert len(api_players) == 2, "Should parse 2 players from JSON"

sarah = api_players.get("111111")
assert sarah is not None
assert sarah["name"] == "Sarah Williams"
assert sarah["city"] == "Denver"
assert sarah["state"] == "CO"
assert sarah["wtn_singles"] == 16.8
assert sarah["wtn_doubles"] == 15.2
assert sarah["ranking"] == 12
print(f"  Sarah Williams: USTA ID={sarah['usta_id']}, WTN Singles={sarah['wtn_singles']}, WTN Doubles={sarah['wtn_doubles']}, Rank={sarah['ranking']}")

david = api_players.get("222222")
assert david is not None
assert david["name"] == "David Lee"
assert david["wtn_doubles"] is None  # Should be None since not provided
print(f"  David Lee: USTA ID={david['usta_id']}, City={david['city']}, State={david['state']}, WTN={david['wtn_singles']}")
print("  PASSED")

# 4. Test playerQuery-style USTA IDs
print("\n[4/4] Testing USTA ID extraction from different URL formats...")

html_query = '<a href="https://www.usta.com/en/home/play/player-profile.html#/?playerQuery=99999">Player One</a>'
players_query = scraper._parse_players_html(html_query)
assert "99999" in players_query
assert players_query["99999"]["name"] == "Player One"
print("  playerQuery=99999 -> extracted correctly")

# Test with ustaId parameter
html_ustaid = '<a href="?ustaId=88888">Player Two</a>'
players_ustaid = scraper._parse_players_html(html_ustaid)
assert "88888" in players_ustaid
print("  ustaId=88888 -> extracted correctly")

# Test with /players/ path
html_path = '<a href="https://playtennis.usta.com/players/77777">Player Three</a>'
players_path = scraper._parse_players_html(html_path)
assert "77777" in players_path
print("  /players/77777 -> extracted correctly")
print("  PASSED")

print("\n" + "=" * 60)
print("Phase 2: ALL TESTS PASSED")
print("=" * 60)
