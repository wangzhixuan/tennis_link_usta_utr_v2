import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

# USTA Credentials
USTA_USER = os.getenv("USTA_USER", "")
USTA_PASS = os.getenv("USTA_PASS", "")

# UTR Credentials
UTR_USER = os.getenv("UTR_USER", "")
UTR_PASS = os.getenv("UTR_PASS", "")

# DB Config
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "tennislink.db"))

# Browser settings
HEADLESS = os.getenv("HEADLESS", "true").lower() in ("true", "1", "yes")

# Match settings
GEO_THRESHOLD_MILES = 50.0  # Distance threshold for hometown/residence proximity matching
