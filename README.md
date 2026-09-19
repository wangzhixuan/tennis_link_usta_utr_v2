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
- **Auto-recovers changed UTR IDs**: if a stored UTR profile no longer exists (HTTP 404 —
  the ID changed, was merged, or removed), it searches for the player's new UTR ID,
  updates the mapping, and fetches the fresh rating automatically.
- Caches everything in SQLite (`tennislink.db`) so re-runs are fast and offline-safe.

## Project layout

```
app.py                 Streamlit web UI (login, run, browse, fix mappings)
main.py                CLI: single tournament run + TSV export
batch_fetch.py         Headless batch refresh of USTA + UTR caches
monitor.py             Progress monitor for batch_fetch
launch_batch.py        Detached launcher for batch_fetch
scripts/
  config.py            .env-driven configuration
  db.py                SQLite schema + cache accessors
  matcher.py           USTA -> UTR matching heuristics + golden mapping loader
  usta_scraper.py      USTA TennisLink scraper (+ playhistory match API)
  utr_scraper.py       UTR API scraper (JWT-first, no-JWT fallback)
  bootstrap.py         Seed/expand the USTA<->UTR mapping DB from one known pair
tests/test_*.py        Standalone (script-style) tests, run directly
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

The main area is split into three tabs: **📖 User Guide** (default), **🎾 Tournament Players**
(the results table), and **🧩 Bootstrap Pairs** (auto-selected when the mapping DB has fewer
than `BOOTSTRAP_MIN_PLAYERS` players).

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

## Bootstrapping the mapping DB (fresh clone)

The golden mapping CSV is **personal data and is not shared**, so a fresh clone starts with an
empty `player_mappings` table and matching is weak. The app detects this (fewer than
`BOOTSTRAP_MIN_PLAYERS` mappings) and shows a **Bootstrap mapping DB** panel. Give it **one
pair you already know** — a player's USTA ID and UTR ID — and it will:

1. Validate that both profiles exist and look like the same person.
2. Compare that player's match history on **both** sides, pairing the *same* physical match by
   **date + score + opponent name/residence**, and save the confident USTA↔UTR pairs.
3. Expand over a couple of layers to build a starter DB of ~`BOOTSTRAP_MAX_PLAYERS` players.

Both **USTA** and **UTR** must be logged in first (the panel shows the status and login buttons;
bootstrap is disabled until both are green). Match correlation only auto-saves high-confidence
pairs; everything is reviewable/correctable later via **Report Wrong Mapping**.

### Manual test on a scratch DB

Same engine from the CLI, without the UI. `--reset` wipes the target DB first so you start empty
(it refuses to wipe the default DB unless `--force`):

```sh
python -m scripts.bootstrap --usta <USTA_ID> --utr <UTR_ID> --db %TEMP%\bootstrap_test.db --reset --max-players 20
```

(`--max-players` includes the seed pair, so `20` collects the seed + up to 19 discovered pairs —
handy for a quick limited test. It also respects `--max-depth`.)

Relevant `.env` knobs: `BOOTSTRAP_MIN_PLAYERS` (default 10), `BOOTSTRAP_MAX_PLAYERS` (30),
`BOOTSTRAP_MAX_DEPTH` (2).

## Running the tests

The tests are plain scripts (no pytest needed):

```sh
python tests/test_setup_and_db.py            # config + SQLite schema/CRUD
python tests/test_usta_tournament_scraper.py # USTA GUID parsing, draws, division filtering
python tests/test_utr_scraper.py             # UTR search/profile/match parsing, login guard
python tests/test_matcher.py                 # name/location + match-history matching engine
python tests/test_cli_and_reporting.py       # CLI args, Excel formatting, full pipeline
python tests/test_usta_profile_rankings.py   # USTA profile/WTN/ranking fetch + persistence
python tests/test_utr_api_live.py            # live UTR API + response edge cases
python tests/test_utr_id_recovery.py         # auto-recovery when a UTR ID changes/disappears
python tests/test_bootstrap.py               # mapping bootstrap + date/score correlation
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