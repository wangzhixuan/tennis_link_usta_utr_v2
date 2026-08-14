# TennisLink — USTA/UTR Player Enrichment Tool

Pulls **USTA tournament draws**, enriches each player with their **WTN / ranking points**
(from USTA profiles) and their **UTR rating** (from UTR, matched by name, hometown, and
optional match history), then stores everything in a local SQLite database and shows it in
a Streamlit web app. Reports export to `data/tournaments/*.tsv`.

## What it does

- Downloads a tournament's draw/participant TSV from USTA TennisLink.
- Fetches each player's USTA profile (WTN, section, rankings, standings points).
- Matches players to UTR profiles via the **golden mapping CSV** first, then a
  name/location heuristic, with a `player_mappings` table that lets you correct bad
  matches (`manual_correct` rows are authoritative).
- Caches everything in SQLite (`tennislink.db`) so re-runs are fast and offline-safe.

## Project layout

```
app.py                 Streamlit web UI (login, run, browse, fix mappings)
main.py                CLI: single tournament run + TSV export
batch_fetch.py         Headless batch refresh of USTA + UTR caches
monitor.py             Progress monitor for batch_fetch
launch_batch.py        Detached launcher for batch_fetch
usta_scraper.py        USTA TennisLink scraper
utr_scraper.py         UTR API scraper (JWT-first, no-JWT fallback)
matcher.py             USTA -> UTR matching heuristics + golden mapping loader
db.py                  SQLite schema + cache accessors
config.py              .env-driven configuration
tests/test_phase*.py   Standalone (script-style) tests, run directly
```

## Prerequisites

- **Python 3.10+** (3.11/3.12 recommended). Get it from python.org or your package
  manager. Check with `python --version`.
- **Git** (for cloning).
- Windows is the primary environment; macOS/Linux work but paths assume a `venv`.
- Live **USTA** and **UTR** accounts (free) — used for logins/session cookies.

## Installing from scratch (first run on a new machine)

```sh
# 1. Clone the repo
git clone <your-repo-url> TennisLink
cd TennisLink

# 2. Create and activate a virtual environment
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
# source venv/bin/activate

# 3. Install Python packages
pip install -r requirements.txt

# 4. Install the Playwright Chromium browser
python -m playwright install chromium
# (Linux only: also run)
# python -m playwright install-deps chromium

# 5. Configure environment
copy .env.example .env        # Windows
# cp .env.example .env        # macOS/Linux
```

### 6. Fill in `.env`

```ini
USTA_USER=your_usta_email
USTA_PASS=your_usta_password
UTR_USER=your_utr_email
UTR_PASS=your_utr_password
DB_PATH=tennislink.db
HEADLESS=true

# Optional: keep empty for defaults
PLAYWRIGHT_USER_DIR=            # default: ~/.tennislink/playwright_profile
GOLDEN_MAPPING_PATH=            # default: <parent>/Tennis/data/usta_to_utr_id_mapping.csv
```

- `PLAYWRIGHT_USER_DIR` is where USTA/UTR login cookies live. Leave it blank; a fresh
  profile is created the first time you log in.
- `GOLDEN_MAPPING_PATH` should point at your `usta_to_utr_id_mapping.csv` (a CSV with
  `usta_id,utr_id` columns). The matcher and batch fetch use it as the source of truth.
  If it's missing the tool still works — it just relies on heuristic matching.
- **Never commit `.env`** — it's in `.gitignore`.

### 7. Verify the install

The SQLite DB is created **automatically** on first import, so you can sanity-check now:

```sh
python -c "import db; db.init_db(); print('DB ready:', db.DB_PATH)"
```

## Running

### Web UI (recommended)

```sh
streamlit run app.py --server.port 8501
```

In the browser:

1. **Check Login Status** → log in to USTA/UTR once (the first time, log in manually —
   a browser window opens and stays logged in for later sessions).
2. Paste a tournament URL or GUID (e.g. `2506261054211502`).
3. Pick a division, then **Run Tournament** to fill the table.
4. Use **Report Wrong Mapping** to fix any bad USTA→UTR links.
5. **Refresh All** re-fetches USTA/UTR data only for players updated before today.

### CLI (single tournament)

```sh
python main.py 2506261054211502 -d "Boys 18"
```

Flags: `--division`, `--no-login`, `--no-profiles`, `--visible`,
`--no-precise-utr`, `--no-cross-ref`. Reports land in `data/tournaments/`.

### Batch cache refresh (background)

Prime/refresh the USTA + UTR caches for every mapping row:

```sh
python batch_fetch.py --usta --utr
python monitor.py        # in a second terminal to watch progress
```

`launch_batch.py` launches it detached from Windows Explorer / a terminal.

## Running the tests

The tests are plain scripts (no pytest needed):

```sh
python tests/test_phase1.py
python tests/test_phase2.py
...
python tests/test_phase7.py
```

Note: tests use the project's SQLite DB and may recreate/clear it. Run them on a scratch
setup, or back up `tennislink.db` first.

## Notes & troubleshooting

- **UTR rate limits**: UTR's JWT-authenticated API can return HTTP 429 for extended
  periods. `utr_scraper.py` automatically falls back to the no-JWT endpoint (integer
  ratings only). Precise decimal ratings are restored once the limit clears and
  `batch_fetch.py` re-fetches.
- **Golden mapping is personal data**: mapping CSVs and everything under `data/`
  (tournament reports + SQLite DBs) are git-ignored on purpose. Share source code, not
  those files.
- **Playwright profile is per-machine**: on a new machine you log in once through the UI;
  the session is stored in `PLAYWRIGHT_USER_DIR` and reused afterward.