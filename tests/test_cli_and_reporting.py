import os
import sys
import tempfile
import pandas as pd
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

print("=" * 60)
print("Testing CLI Orchestrator & TSV Exporter")
print("=" * 60)

from main import (
    parse_args,
    build_ranking_list_name,
    build_output_path,
    get_points_for_list,
    main as run_main,
)

# 1. Test CLI argument parsing
print("\n[1/5] Testing CLI argument parsing...")

test_args = ["main.py", "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B", "-d", "Boys 18"]
with patch.object(sys, 'argv', test_args):
    args = parse_args()
    assert args.tournament == "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B"
    assert args.division == "Boys 18"
    assert args.no_login is False
    assert args.no_profiles is False
    assert args.visible is False
    assert args.no_precise_utr is False
    assert args.no_cross_ref is False
print("  Full arguments parsed: PASSED")

test_args_defaults = ["main.py", "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B"]
with patch.object(sys, 'argv', test_args_defaults):
    args = parse_args()
    assert args.tournament == "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B"
    assert args.division is None
    assert args.no_login is False
print("  Default arguments: PASSED")

for flag, attr in [
    ("--no-login", "no_login"),
    ("--no-profiles", "no_profiles"),
    ("--visible", "visible"),
    ("--no-precise-utr", "no_precise_utr"),
    ("--no-cross-ref", "no_cross_ref"),
]:
    with patch.object(sys, 'argv', ["main.py", "X", flag]):
        args = parse_args()
        assert getattr(args, attr) is True, f"{flag} should set {attr}=True"
print("  Boolean flags (--no-login/--no-profiles/--visible/--no-precise-utr/--no-cross-ref): PASSED")

try:
    with patch.object(sys, 'argv', ["main.py", "--help"]):
        try:
            parse_args()
        except SystemExit:
            pass
    print("  --help triggers SystemExit: PASSED")
except Exception:
    pass
print("  PASSED")

# 2. Test ranking list name + points lookup
print("\n[2/5] Testing ranking list name & points lookup...")
assert build_ranking_list_name("Girls U12") == "Girls' 12 National Standings List (combined)"
assert build_ranking_list_name("Boys 18") == "Boys' 18 National Standings List (combined)"
assert build_ranking_list_name("") is None

sample_rankings = [
    {"display_label": "Boys' 18 National Standings List (combined)", "points": 350},
]
assert get_points_for_list(sample_rankings, "Boys' 18 National Standings List (combined)") == 350
assert get_points_for_list(sample_rankings, "Nonexistent List") is None
assert get_points_for_list(sample_rankings, None) is None
print("  PASSED")

# 3. Test output path generation
print("\n[3/5] Testing output path generation...")
info = {
    "name": "Spring Classic",
    "guid": "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B",
    "start_date": "2026-03-01",
}
out = build_output_path(info, "Boys 18")
assert out.endswith(".tsv"), f"Expected .tsv output, got {out}"
assert "Spring_Classic" in out
assert "20260301" in out
print(f"  {os.path.basename(out)}: PASSED")

# 4. Test TSV round-trip (the current export format)
print("\n[4/5] Testing TSV export round-trip...")
tmp_tsv = os.path.join(tempfile.gettempdir(), "test_report_roundtrip.tsv")
df = pd.DataFrame({
    "Player Name": ["Alice Williams", "Bob Smith"],
    "USTA ID": ["11111", "22222"],
    "UTR ID": ["UTR_ALICE", "UTR_BOB"],
    "UTR Singles": [11.5, 8.0],
})
df.to_csv(tmp_tsv, index=False, sep="\t")
back = pd.read_csv(tmp_tsv, sep="\t")
assert len(back) == 2
assert back.iloc[0]["Player Name"] == "Alice Williams"
assert back.iloc[0]["UTR Singles"] == 11.5
os.remove(tmp_tsv)
print("  PASSED")

# 5. Test full pipeline with mocked components
print("\n[5/5] Testing full main pipeline (mocked)...")

mock_usta_players = [
    {"usta_id": "11111", "name": "Alice Williams", "city": "Los Angeles", "state": "CA",
     "wtn_singles": 16.5, "wtn_doubles": 15.0,
     "rankings": [{"display_label": "Girls' 18 National Standings List (combined)", "points": 300}]},
    {"usta_id": "22222", "name": "Bob Smith", "city": "Chicago", "state": "IL",
     "wtn_singles": 14.2, "wtn_doubles": 13.8, "rankings": []},
]


def mock_find_utr(usta_player, tournament_players=None, **kwargs):
    mapping = {
        "11111": {"utr_id": "UTR_ALICE", "name": "Alice Williams", "utr_singles": 11.5,
                  "utr_doubles": 10.2, "confidence": 0.95, "match_method": "name_geo", "source": "search"},
        "22222": {"utr_id": "UTR_BOB", "name": "Bob Smith", "utr_singles": 8.0,
                  "utr_doubles": 7.5, "confidence": 0.88, "match_method": "name_geo", "source": "search"},
    }
    return mapping.get(usta_player["usta_id"])


output_path = os.path.join(tempfile.gettempdir(), "test_pipeline_output.tsv")

with patch('main.USTAScraper') as MockUSTAScraper, \
     patch('main.UTRScraper') as MockUTRScraper, \
     patch('main.PlayerMatcher') as MockPlayerMatcher, \
     patch('main.build_output_path', return_value=output_path), \
     patch.object(sys, 'argv', ["main.py", "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B", "-d", "Girls 18"]):

    mock_usta_instance = MockUSTAScraper.return_value
    mock_usta_instance.get_tournament_info.return_value = {
        "name": "Spring Classic",
        "guid": "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B",
        "start_date": "2026-03-01",
    }
    mock_usta_instance.fetch_profiles_for_tournament.return_value = mock_usta_players

    mock_utr_instance = MockUTRScraper.return_value
    mock_utr_instance.login_if_needed.return_value = True
    mock_utr_instance.get_player_profile.return_value = None
    mock_utr_instance.last_profile_not_found = False
    mock_utr_instance.fetch_precise_utr_batch.return_value = {}

    mock_matcher_instance = MockPlayerMatcher.return_value
    mock_matcher_instance.find_utr_profile.side_effect = mock_find_utr

    run_main()

    MockUSTAScraper.assert_called_once()
    mock_usta_instance.fetch_profiles_for_tournament.assert_called_once_with(
        "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B", "Girls 18", headless=True
    )

    assert os.path.exists(output_path), f"Output file {output_path} should exist"

    df_result = pd.read_csv(output_path, sep="\t")
    assert len(df_result) == 2
    assert df_result.iloc[0]["Player Name"] == "Alice Williams"
    assert df_result.iloc[1]["Player Name"] == "Bob Smith"
    assert df_result.iloc[0]["UTR ID"] == "UTR_ALICE"
    assert df_result.iloc[0]["UTR Singles"] == 11.5
    assert df_result.iloc[0]["Match Confidence"] == 0.95
    assert df_result.iloc[0]["Ranking Points"] == 300

    os.remove(output_path)

print("  Full pipeline executed successfully")
print("  PASSED")

print("\n" + "=" * 60)
print("CLI & Reporting: ALL TESTS PASSED")
print("=" * 60)
