import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Phase 1 Test: verify imports, config, and database
print("=" * 60)
print("Phase 1: Testing Project Setup")
print("=" * 60)

# 1. Verify config loads
print("\n[1/4] Testing config...")
from scripts import config
assert config.BASE_DIR.exists(), "BASE_DIR should exist"
assert config.DB_PATH.endswith(".db"), "DB_PATH should be a .db file"
print(f"  BASE_DIR:  {config.BASE_DIR}")
print(f"  DB_PATH:   {config.DB_PATH}")
print(f"  HEADLESS:  {config.HEADLESS}")
print("  PASSED")

# 2. Verify DB module imports and table creation
print("\n[2/4] Testing database initialization...")
from scripts.db import get_connection, init_db, save_usta_player_profile, get_usta_player_profile, save_utr_player_profile, get_utr_player_profile, save_mapping, get_mapping

# Use a temporary database for testing
from scripts import db
original_db_path = db.DB_PATH
db.DB_PATH = os.path.join(tempfile.gettempdir(), "tennislink_test.db")

# Re-init with test DB
db.init_db()

with db.get_connection() as conn:
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    table_names = [t[0] for t in tables]
    print(f"  Tables created: {table_names}")
    assert "usta_player_profiles" in table_names, "usta_player_profiles table should exist"
    assert "utr_player_profiles" in table_names, "utr_player_profiles table should exist"
    assert "player_mappings" in table_names, "player_mappings table should exist"
print("  PASSED")

# 3. Test CRUD on usta_player_profiles

save_usta_player_profile("TEST001", "John Doe", city="Austin", state="TX",
                         wtn_singles=12.5, wtn_doubles=14.2,
                         rankings=[{"display_label": "Boys' 14 National Standings List (combined)", "points": 42}])
row = get_usta_player_profile("TEST001")
import json
assert row["name"] == "John Doe"
assert row["city"] == "Austin"
assert row["state"] == "TX"
assert row["wtn_singles"] == 12.5
assert row["wtn_doubles"] == 14.2
ranking_data = json.loads(row["ranking_json"]) if row["ranking_json"] else []
assert ranking_data[0]["points"] == 42
print("  usta_player_profiles: CREATE + READ = PASSED")

# Test update
save_usta_player_profile("TEST001", "John Doe", city="Dallas", state="TX", wtn_singles=11.0)
row = get_usta_player_profile("TEST001")
assert row["city"] == "Dallas"
assert row["wtn_doubles"] is None
print("  usta_player_profiles: UPDATE = PASSED")

# Test utr_player_profiles CRUD
save_utr_player_profile("UTR001", "John Doe", city="Austin", state="TX",
                         utr_singles=8.5, utr_doubles=9.0)
row = get_utr_player_profile("UTR001")
assert row["utr_singles"] == 8.5
assert row["utr_doubles"] == 9.0
print("  utr_player_profiles: CRUD = PASSED")

# Test mappings CRUD
save_mapping("TEST001", "UTR001", "test_script", 0.95)
row = get_mapping("TEST001")
assert row["utr_id"] == "UTR001"
assert row["confidence"] == 0.95
assert row["match_method"] == "test_script"
print("  player_mappings: CRUD = PASSED")
print("  PASSED")

# 4. Verify requirements.txt packages are importable
print("\n[4/4] Testing third-party imports...")
import pandas
import openpyxl
import dotenv
import requests
print("  All packages import successfully!")
print("  PASSED")

# Cleanup test DB - force close all connections
import sqlite3
sqlite3.connect(db.DB_PATH).close()  # Close any lingering connections
for _ in range(3):
    try:
        os.remove(db.DB_PATH)
        break
    except PermissionError:
        import time
        time.sleep(0.5)
        # Force garbage collection
        import gc
        gc.collect()

print("\n" + "=" * 60)
print("Phase 1: ALL TESTS PASSED")
print("=" * 60)
