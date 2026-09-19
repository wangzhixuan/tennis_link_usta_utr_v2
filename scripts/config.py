import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# USTA Credentials
USTA_USER = os.getenv("USTA_USER", "")
USTA_PASS = os.getenv("USTA_PASS", "")

# UTR Credentials
UTR_USER = os.getenv("UTR_USER", "")
UTR_PASS = os.getenv("UTR_PASS", "")

# DB Config
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "tennislink.db"))

# Playwright persistent browser profile (stores USTA/UTR login cookies).
# Override with PLAYWRIGHT_USER_DIR in .env. On a brand-new machine this
# falls back to ~/.tennislink/playwright_profile; it keeps the legacy path
# if that still exists so existing logins keep working.
def _resolve_playwright_dir() -> str:
    env = os.getenv("PLAYWRIGHT_USER_DIR")
    if env:
        return env
    legacy = Path(r"C:\Users\cicic\AppData\Local\Temp\opencode\playwright_profile")
    if legacy.exists():
        return str(legacy)
    return str(Path.home() / ".tennislink" / "playwright_profile")

PLAYWRIGHT_USER_DIR = _resolve_playwright_dir()

# Golden USTA->UTR mapping CSV, used by batch_fetch.py and the matcher's
# cross-reference heuristic. Override with GOLDEN_MAPPING_PATH in .env.
GOLDEN_MAPPING_PATH = os.getenv(
    "GOLDEN_MAPPING_PATH",
    str(BASE_DIR.parent / "Tennis" / "data" / "usta_to_utr_id_mapping.csv"),
)

# Browser settings
HEADLESS = os.getenv("HEADLESS", "true").lower() in ("true", "1", "yes")

# Match settings
GEO_THRESHOLD_MILES = 50.0  # Distance threshold for hometown/residence proximity matching

# Mapping bootstrap (fresh clone starts with an empty player_mappings DB).
# When fewer than BOOTSTRAP_MIN_PLAYERS mappings exist, the app offers to seed
# the DB from a known USTA<->UTR pair and expand via match histories.
BOOTSTRAP_MIN_PLAYERS = int(os.getenv("BOOTSTRAP_MIN_PLAYERS", "10"))
BOOTSTRAP_MAX_PLAYERS = int(os.getenv("BOOTSTRAP_MAX_PLAYERS", "30"))
BOOTSTRAP_MAX_DEPTH = int(os.getenv("BOOTSTRAP_MAX_DEPTH", "2"))
