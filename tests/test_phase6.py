import os
import sys
import json
import tempfile
import pandas as pd
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

print("=" * 60)
print("Phase 6: Testing USTA Profile & Ranking Fetching")
print("=" * 60)

# Fixture: captured playerInfo API response
PLAYER_INFO_FIXTURE = {
    "data": [{
        "name": "Solon Wang",
        "city": "San Diego",
        "state": "CA",
        "gender": "MALE",
        "ageCategory": "10_AND_UNDER",
        "section": {"name": "Southern California"},
        "district": {"name": "San Diego"},
        "itfTennisId": "WAN9724852",
        "nationality": "USA",
        "ratings": {
            "ballColorRating": "Green Ball Level 2",
            "competitionLevelBallColor": "Yellow",
            "wtn": [
                {"type": "SINGLE", "tennisNumber": 33.8, "confidence": 100, "ratingDate": "2026-07-15"},
                {"type": "DOUBLE", "tennisNumber": 33.67, "confidence": 60, "ratingDate": "2026-07-15"}
            ]
        }
    }]
}

RANKINGS_FIXTURE = {
    "player": {
        "rankings": [
            {
                "displayLabel": "Boys' 12 Southern California Quota List (Combined)",
                "ageRestriction": "Y12",
                "listType": "QUOTA",
                "matchFormat": None,
                "rankListGender": "M",
                "rank": {"national": 123, "section": 123, "district": 23},
                "points": 350,
                "pointsRecord": {"singlesPoints": 331, "doublesPoints": 127, "bonusPoints": 0},
                "record": {"win": 40, "loss": 26},
                "trendDirection": "no change",
                "publishDate": "2026-07-01T16:58:39.141Z"
            },
            {
                "displayLabel": "Boys' 12 National Standings List (combined)",
                "ageRestriction": "Y12",
                "listType": "STANDING",
                "matchFormat": None,
                "rankListGender": "M",
                "rank": {"national": 1354, "section": 125, "district": 23},
                "points": 350,
                "pointsRecord": {"singlesPoints": 331, "doublesPoints": 127, "bonusPoints": 0},
                "record": {"win": 39, "loss": 23},
                "trendDirection": "no change",
                "publishDate": "2026-07-15T11:43:11.632Z"
            }
        ]
    }
}

# 1. Test _parse_and_save_profile
print("\n[1/5] Testing _parse_and_save_profile with fixture data...")
from usta_scraper import USTAScraper
from db import init_db, get_usta_player_profile, get_usta_rankings

init_db()

scraper = USTAScraper()
api_data = {"info": PLAYER_INFO_FIXTURE, "rankings": RANKINGS_FIXTURE}
result = scraper._parse_and_save_profile("TEST_FIXTURE", api_data)

assert result["usta_id"] == "TEST_FIXTURE"
assert result["name"] == "Solon Wang"
assert result["city"] == "San Diego"
assert result["state"] == "CA"
assert result["section"] == "Southern California"
assert result["district"] == "San Diego"
assert result["gender"] == "MALE"
assert result["age_category"] == "10_AND_UNDER"
assert result["ball_color"] == "Green Ball Level 2"
assert result["competition_level"] == "Yellow"
assert result["itf_tennis_id"] == "WAN9724852"
assert result["nationality"] == "USA"
assert result["wtn_singles"] == 33.8
assert result["wtn_singles_confidence"] == 100
assert result["wtn_singles_date"] == "2026-07-15"
assert result["wtn_doubles"] == 33.67
assert result["wtn_doubles_confidence"] == 60
assert result["wtn_doubles_date"] == "2026-07-15"
assert len(result["rankings"]) == 2

r0 = result["rankings"][0]
assert r0["display_label"] == "Boys' 12 Southern California Quota List (Combined)"
assert r0["rank_national"] == 123
assert r0["rank_section"] == 123
assert r0["rank_district"] == 23
assert r0["points"] == 350
assert r0["points_singles"] == 331
assert r0["points_doubles"] == 127
assert r0["points_bonus"] == 0
assert r0["wins"] == 40
assert r0["losses"] == 26
assert r0["trend_direction"] == "no change"
assert r0["publish_date"] == "2026-07-01T16:58:39.141Z"
print("  Parsed profile data correct: PASSED")

# 2. Verify data saved to DB
print("\n[2/5] Verifying DB persistence...")
cached = get_usta_player_profile("TEST_FIXTURE")
assert cached is not None
assert cached["name"] == "Solon Wang"
assert cached["wtn_singles"] == 33.8
assert cached["section"] == "Southern California"
assert cached["ball_color"] == "Green Ball Level 2"
print(f"  Profile cached: {cached['name']}, WTN S={cached['wtn_singles']}")

ranks = get_usta_rankings("TEST_FIXTURE")
assert len(ranks) == 2
assert ranks[0]["display_label"] == "Boys' 12 Southern California Quota List (Combined)"
assert ranks[0]["rank_national"] == 123
assert ranks[0]["wins"] == 40
print(f"  Rankings saved: {len(ranks)} lists")
print("  PASSED")

# 3. Test empty profile handling
print("\n[3/5] Testing empty/missing profile handling...")
empty_result = scraper._parse_and_save_profile("EMPTY_FIXTURE", {})
assert empty_result == {}
print("  Empty API data returns empty dict: PASSED")

no_info_result = scraper._parse_and_save_profile("NO_INFO", {"info": {"data": [{}]}})
assert no_info_result == {}
print("  Missing WTN/rankings returns empty dict: PASSED")
print("  PASSED")

# 4. Test CLI argument parsing for new flags
print("\n[4/5] Testing new CLI flags...")
from main import parse_args

with patch.object(sys, 'argv', ["main.py", "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B", "--profiles"]):
    args = parse_args()
    assert args.profiles == True
    assert args.visible == False
print("  --profiles flag: PASSED")

with patch.object(sys, 'argv', ["main.py", "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B", "--profiles", "--visible"]):
    args = parse_args()
    assert args.profiles == True
    assert args.visible == True
print("  --visible flag: PASSED")

with patch.object(sys, 'argv', ["main.py", "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B"]):
    args = parse_args()
    assert args.profiles == False
    assert args.visible == False
print("  Default (no flags): PASSED")
print("  PASSED")

# 5. Test enhanced pipeline with profiles (mocked)
print("\n[5/5] Testing main pipeline with profile fetching (mocked)...")

profile_enriched_players = [
    {
        "usta_id": "11111",
        "name": "Alice Williams",
        "city": "Los Angeles", "state": "CA",
        "wtn_singles": 33.8, "wtn_doubles": 33.67,
        "section": "Southern California",
        "rankings": [
            {"display_label": "Girls' 12 National Standings", "rank_national": 123, "rank_section": 12,
             "rank_district": 3, "points": 350, "wins": 40, "losses": 26}
        ]
    },
    {
        "usta_id": "22222",
        "name": "Bob Smith",
        "city": "Chicago", "state": "IL",
        "wtn_singles": 14.2, "wtn_doubles": 13.8,
        "rankings": []
    },
]

output_path = os.path.join(tempfile.gettempdir(), "test_phase6_output.xlsx")

def mock_find_utr(usta_player, tournament_players=None):
    mapping = {
        "11111": {"utr_id": "UTR_ALICE", "utr_singles": 11.5, "utr_doubles": 10.2,
                  "confidence": 0.95, "match_method": "name_geo", "source": "search"},
        "22222": {"utr_id": "UTR_BOB", "utr_singles": 8.0, "utr_doubles": 7.5,
                  "confidence": 0.88, "match_method": "name_geo", "source": "search"},
    }
    return mapping.get(usta_player["usta_id"])

with patch('main.USTAScraper') as MockUSTAScraper, \
     patch('main.UTRScraper') as MockUTRScraper, \
     patch('main.PlayerMatcher') as MockPlayerMatcher, \
     patch.object(sys, 'argv', ["main.py", "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B",
                                 "-d", "Girls 12", "-o", output_path, "--profiles"]):

    mock_usta_instance = MockUSTAScraper.return_value
    mock_usta_instance.fetch_profiles_for_tournament.return_value = profile_enriched_players

    mock_utr_instance = MockUTRScraper.return_value
    mock_utr_instance.login_if_needed.return_value = True

    mock_matcher_instance = MockPlayerMatcher.return_value
    mock_matcher_instance.find_utr_profile.side_effect = mock_find_utr

    from main import main
    main()

    MockUSTAScraper.assert_called_once()
    mock_usta_instance.fetch_profiles_for_tournament.assert_called_once_with(
        "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B", "Girls 12", headless=True
    )

    assert os.path.exists(output_path), f"Output file {output_path} should exist"

    xls = pd.ExcelFile(output_path)
    assert "Players" in xls.sheet_names
    assert "Rankings" in xls.sheet_names

    df = pd.read_excel(output_path, sheet_name="Players")
    assert len(df) == 2
    assert "Best National Rank" in df.columns
    assert "Best Section Rank" in df.columns
    assert "Top Ranking List" in df.columns
    assert "WTN Singles" in df.columns

    alice = df[df["Player Name"] == "Alice Williams"].iloc[0]
    assert alice["Best National Rank"] == 123
    assert alice["Best Section Rank"] == 12
    assert alice["Top Ranking List"] == "Girls' 12 National Standings"
    assert alice["WTN Singles"] == 33.8

    bob = df[df["Player Name"] == "Bob Smith"].iloc[0]
    import math
    assert math.isnan(bob["Best National Rank"]), f"Expected NaN, got {bob['Best National Rank']}"
    assert isinstance(bob["Top Ranking List"], float) and math.isnan(bob["Top Ranking List"])

    rankings_df = pd.read_excel(output_path, sheet_name="Rankings")
    assert len(rankings_df) == 1
    assert rankings_df.iloc[0]["Player Name"] == "Alice Williams"

    try:
        os.remove(output_path)
    except PermissionError:
        pass

print("  Enhanced pipeline with profiles: PASSED")
print("  PASSED")

print("\n" + "=" * 60)
print("Phase 6: ALL TESTS PASSED")
print("=" * 60)
