import os
import sys
import json
import tempfile
import pandas as pd
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Use a scratch database so the real tennislink.db isn't touched
from scripts import db
db.DB_PATH = os.path.join(tempfile.gettempdir(), "tennislink_test_profiles.db")
db.init_db()

print("=" * 60)
print("Testing USTA Profile & Ranking Fetching")
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
from scripts.usta_scraper import USTAScraper
from scripts.db import get_usta_player_profile, get_usta_rankings

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

# 4. Test CLI argument parsing for profile flags
print("\n[4/5] Testing profile-related CLI flags...")
from main import parse_args

with patch.object(sys, 'argv', ["main.py", "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B"]):
    args = parse_args()
    assert args.no_profiles is False, "Profiles should be fetched by default"
    assert args.visible is False
print("  Default (profiles enabled): PASSED")

with patch.object(sys, 'argv', ["main.py", "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B", "--no-profiles"]):
    args = parse_args()
    assert args.no_profiles is True
print("  --no-profiles flag: PASSED")

with patch.object(sys, 'argv', ["main.py", "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B", "--visible"]):
    args = parse_args()
    assert args.visible is True
print("  --visible flag: PASSED")
print("  PASSED")

# 5. Test pipeline with profile-enriched players (mocked)
print("\n[5/5] Testing main pipeline with profile fetching (mocked)...")

profile_enriched_players = [
    {
        "usta_id": "11111",
        "name": "Alice Williams",
        "city": "Los Angeles", "state": "CA",
        "wtn_singles": 33.8, "wtn_doubles": 33.67,
        "section": "Southern California",
        "rankings": [
            {"display_label": "Girls' 12 National Standings List (combined)",
             "rank_national": 123, "rank_section": 12,
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

output_path = os.path.join(tempfile.gettempdir(), "test_profiles_output.tsv")


def mock_find_utr(usta_player, tournament_players=None, **kwargs):
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
     patch('main.build_output_path', return_value=output_path), \
     patch.object(sys, 'argv', ["main.py", "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B", "-d", "Girls 12"]):

    mock_usta_instance = MockUSTAScraper.return_value
    mock_usta_instance.get_tournament_info.return_value = {
        "name": "Test Event",
        "guid": "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B",
        "start_date": "2026-03-01",
    }
    mock_usta_instance.fetch_profiles_for_tournament.return_value = profile_enriched_players

    mock_utr_instance = MockUTRScraper.return_value
    mock_utr_instance.login_if_needed.return_value = True
    mock_utr_instance.get_player_profile.return_value = None
    mock_utr_instance.last_profile_not_found = False
    mock_utr_instance.fetch_precise_utr_batch.return_value = {}

    mock_matcher_instance = MockPlayerMatcher.return_value
    mock_matcher_instance.find_utr_profile.side_effect = mock_find_utr

    from main import main
    main()

    MockUSTAScraper.assert_called_once()
    mock_usta_instance.fetch_profiles_for_tournament.assert_called_once_with(
        "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B", "Girls 12", headless=True
    )

    assert os.path.exists(output_path), f"Output file {output_path} should exist"

    df = pd.read_csv(output_path, sep="\t")
    assert len(df) == 2
    assert "WTN Singles" in df.columns
    assert "Ranking Points" in df.columns

    alice = df[df["Player Name"] == "Alice Williams"].iloc[0]
    assert alice["WTN Singles"] == 33.8
    assert alice["Ranking Points"] == 350
    assert alice["UTR ID"] == "UTR_ALICE"

    try:
        os.remove(output_path)
    except PermissionError:
        pass

print("  Profile-enriched pipeline: PASSED")
print("  PASSED")

# Cleanup scratch DB
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
print("USTA Profile & Rankings: ALL TESTS PASSED")
print("=" * 60)
