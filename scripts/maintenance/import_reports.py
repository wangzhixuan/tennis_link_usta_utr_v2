"""Import existing data/tournaments/*.tsv reports into the checked_events history.

Filenames look like:
  Tournament.<yyyymmdd>.<name>.<guid8>.<division>.<event8?>.tsv
The full tournament GUID is resolved by name (TextQuery) or by a geo+date search
using the first player's hometown. Finder reports (with an 8-char event suffix) are
matched to the full event id via GetTournament; division-only reports are imported
as single-run checks.

Run (from repo root):
    python -m scripts.maintenance.import_reports [--dry-run] [--data-dir data/tournaments]
"""
import os
import re
import csv
import glob
import datetime
import argparse
import logging

import requests

from scripts.service import db
from scripts.service.tournament_finder import (SEARCH_URL, SEARCH_HEADERS,
                                       geocode_location, fetch_tournament_events)

logger = logging.getLogger("import_reports")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

TEXT_QUERY_URL = ("https://prd-usta-kube.clubspark.pro/unified-search-api/api/Search/"
                  "tournaments/TextQuery")
TEXT_HEADERS = {"User-Agent": "Mozilla/5.0"}

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DATA_DIR = os.path.join(BASE_DIR, "data", "tournaments")


def parse_report_path(path):
    base = os.path.basename(path)[:-4]
    parts = base.split(".")
    if len(parts) < 5 or parts[0] != "Tournament":
        return None
    date, name_part, guid8 = parts[1], parts[2], parts[3].lower()
    rest = parts[4:]
    event8 = None
    if rest and re.fullmatch(r"[0-9A-Fa-f]{8}", rest[-1]):
        event8 = rest[-1].lower()
        rest = rest[:-1]
    return {
        "path": path,
        "date": date,
        "name": name_part.replace("_", " "),
        "guid8": guid8,
        "division": " ".join(rest).replace("_", " ").strip(),
        "event8": event8,
    }


def read_roster(path):
    roster = []
    city = None
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            usta_id = (row.get("USTA ID") or "").strip()
            if not usta_id:
                continue
            roster.append({
                "usta_id": usta_id,
                "name": (row.get("Player Name") or "").strip(),
                "status": (row.get("Status") or "").strip(),
            })
            if not city and row.get("Hometown"):
                city = row["Hometown"].strip()
    return roster, city


def _text_query(name, guid8):
    words = name.split()
    texts = [name]
    for n in (5, 4, 3, 2):
        if len(words) > n:
            texts.append(" ".join(words[:n]))
    for text in texts:
        try:
            r = requests.get(TEXT_QUERY_URL,
                             params={"indexSchema": "tournament", "limit": 10, "text": text},
                             headers=TEXT_HEADERS, timeout=20)
            data = r.json()
        except Exception:
            continue
        for it in data.get("items") or []:
            if str(it.get("id", "")).lower().startswith(guid8):
                return it.get("id")
    return None


def _geo_search(city, date_str, guid8):
    coords = geocode_location(city) if city else None
    if not coords:
        return None
    try:
        d = datetime.date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
    except (ValueError, TypeError):
        return None
    lat, lon = coords
    for pad in (3, 7, 14, 30):
        lo = (d - datetime.timedelta(days=pad)).strftime("%Y-%m-%dT00:00:00.000Z")
        hi = (d + datetime.timedelta(days=pad)).strftime("%Y-%m-%dT23:59:59.999Z")
        body = {
            "options": {"size": 100, "from": 0, "sortKey": "date",
                        "latitude": lat, "longitude": lon},
            "filters": [
                {"key": "publish-target", "items": [{"value": 1}]},
                {"key": "date-range", "operator": "Or",
                 "items": [{"minDate": lo, "maxDate": hi}]},
                {"key": "distance", "operator": "Or", "items": [{"value": 500}]},
            ],
        }
        try:
            r = requests.post(SEARCH_URL, json=body, headers=SEARCH_HEADERS, timeout=30)
            data = r.json()
        except Exception:
            continue
        for res in data.get("searchResults") or []:
            it = res.get("item") or {}
            if str(it.get("id", "")).lower().startswith(guid8):
                return it.get("id")
    return None


def resolve_tournament(meta, city):
    return _text_query(meta["name"], meta["guid8"]) or _geo_search(city, meta["date"], meta["guid8"])


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def resolve_event(full_id, event8, division_label):
    if not event8:
        return None
    try:
        events = fetch_tournament_events(full_id)
    except Exception:
        return None
    for eid, ev in events.items():
        if str(eid).lower().startswith(event8):
            return ev
    target = _norm(division_label)
    for ev in events.values():
        if _norm(ev.get("label")) == target:
            return ev
    return None


def import_reports(data_dir=DEFAULT_DATA_DIR, dry_run=False):
    files = sorted(glob.glob(os.path.join(data_dir, "*.tsv")), key=os.path.getmtime)
    stats = {"files": len(files), "imported": 0, "unresolved": 0,
             "no_roster": 0, "skipped_newer": 0, "would": 0}
    for path in files:
        meta = parse_report_path(path)
        if not meta:
            logger.info(f"skip (bad name): {os.path.basename(path)}")
            continue
        roster, city = read_roster(path)
        if not roster:
            stats["no_roster"] += 1
            logger.info(f"skip (no roster): {os.path.basename(path)}")
            continue

        full_id = resolve_tournament(meta, city)
        if not full_id:
            stats["unresolved"] += 1
            logger.warning(f"unresolved tournament: {meta['name']} ({meta['guid8']})")
            continue

        event = resolve_event(full_id, meta["event8"], meta["division"])
        if event:
            event_id = event.get("id")
            label = event.get("label") or meta["division"]
            level = event.get("level")
            division = event.get("division") or {}
        else:
            event_id = None
            label = meta["division"] or "All divisions"
            level = None
            division = {}

        event_key = f"{full_id}:{event_id}" if event_id else f"{full_id}:division:{label}"
        event_date = (f"{meta['date'][:4]}-{meta['date'][4:6]}-{meta['date'][6:8]}"
                      if len(meta["date"]) == 8 else None)
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path)).isoformat()

        existing = db.get_checked_event(event_key)
        if existing and (existing.get("last_checked") or "") > mtime:
            stats["skipped_newer"] += 1
            continue

        if dry_run:
            stats["would"] += 1
            logger.info(f"[dry] {meta['name']} — {label}: {len(roster)} players")
            continue

        db.save_checked_event(
            event_key, full_id, event_id, meta["name"], label, level, division, roster,
            event_date=event_date, checked_at=mtime,
        )
        stats["imported"] += 1
        logger.info(f"imported {meta['name']} — {label}: {len(roster)} players")

    logger.info(f"Done. {stats}")
    try:
        from scripts.maintenance.merge_history import merge_history
        merge_history()
    except Exception as e:
        logger.warning(f"History merge after import failed: {e}")
    return stats


def main():
    p = argparse.ArgumentParser(description="Import on-disk TSV reports into checked_events.")
    p.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    import_reports(args.data_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
