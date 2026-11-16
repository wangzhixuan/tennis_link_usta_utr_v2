"""Background re-check of players whose UTR rating is 0.00.

For each mapped player with a 0.00 UTR profile, one player per interval (default 60s):
  1. Re-fetch the current UTR profile (1 request).
  2. If still 0.00, do ONE UTR name search (1 request) for a rated duplicate and,
     on a strong name+location match with a positive rating, remap the player.
Everything is logged to check_zero_utr.log. A PID file prevents double starts.

Run (from repo root):
    python -m scripts.maintenance.check_zero_utr [--interval 60] [--dry-run] [--force]
"""
import os
import sys
import time
import argparse
import logging

from scripts.service import db
from scripts.service.utr_scraper import UTRScraper
from scripts.service.matcher import PlayerMatcher
from scripts.service.db import save_mapping

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG_PATH = os.path.join(BASE_DIR, "check_zero_utr.log")
PID_PATH = os.path.join(BASE_DIR, "check_zero_utr.pid")

logger = logging.getLogger("check_zero_utr")
logger.setLevel(logging.INFO)
_fh = logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_fh)


def load_targets():
    with db.get_connection() as conn:
        rows = conn.execute(
            """
            SELECT m.usta_id, m.utr_id,
                   u.name AS usta_name, u.city, u.state, u.wtn_singles,
                   r.name AS utr_name, r.utr_singles,
                   r.city AS utr_city, r.state AS utr_state
            FROM player_mappings m
            JOIN usta_player_profiles u ON u.usta_id = m.usta_id
            JOIN utr_player_profiles r ON r.utr_id = m.utr_id
            WHERE r.utr_singles IS NULL OR r.utr_singles = 0
            """
        ).fetchall()
    return [dict(r) for r in rows]


def _wait_if_rate_limited():
    if UTRScraper.is_rate_limited():
        wait = UTRScraper.rate_limit_remaining() + 5
        logger.warning(f"UTR rate limit active; sleeping {wait}s")
        time.sleep(wait)


def check_one(utr, matcher, target, dry_run=False):
    usta_id = str(target["usta_id"])
    old_utr = str(target["utr_id"])
    name = target.get("usta_name") or ""
    city, state = target.get("city"), target.get("state")

    logger.info(f"Checking {name} (USTA {usta_id}) current UTR {old_utr} ...")

    profile = utr.get_player_profile(old_utr)
    _wait_if_rate_limited()

    if profile and (profile.get("utr_singles") or 0) > 0:
        logger.info(f"  recovered: UTR {old_utr} now rated {profile.get('utr_singles')}")
        return "recovered"

    if utr.last_profile_not_found:
        logger.warning(f"  current UTR {old_utr} no longer exists; searching for a replacement")

    candidates = utr.search_players(name, city=city, state=state)
    _wait_if_rate_limited()

    best = None
    for c in candidates:
        cid = str(c.get("utr_id"))
        if cid == old_utr:
            continue
        rating = c.get("utr_singles")
        try:
            rating_f = float(rating) if rating is not None else None
        except (TypeError, ValueError):
            rating_f = None
        if not rating_f or rating_f <= 0:
            continue
        name_sim = matcher.name_similarity(name, c.get("name") or "")
        loc = matcher.location_score(city, state, c.get("city"), c.get("state"))
        if name_sim < 0.9 or loc < 0.75:
            continue
        confidence = 0.6 * name_sim + 0.4 * loc
        if best is None or confidence > best[0]:
            best = (confidence, c)

    if best:
        confidence, c = best
        new_utr = str(c["utr_id"])
        logger.info(
            f"  rated duplicate found: {new_utr} ({c.get('name')}, UTR {c.get('utr_singles')}, "
            f"conf {confidence:.2f})"
        )
        if not dry_run:
            save_mapping(usta_id, new_utr, "active_duplicate", round(confidence, 3))
            logger.info(f"  remapped USTA {usta_id} -> UTR {new_utr}")
        return "remapped"
    else:
        logger.info("  no rated duplicate found; rating remains 0.00")
        return "still-zero"


def parse_args():
    p = argparse.ArgumentParser(description="Re-check players with 0.00 UTR ratings.")
    p.add_argument("--interval", type=int, default=60,
                   help="Seconds between players (default 60 = one per minute)")
    p.add_argument("--dry-run", action="store_true", help="Report only; do not change mappings")
    p.add_argument("--force", action="store_true", help="Start even if a PID file exists")
    p.add_argument("--limit", type=int, default=0, help="Process at most N players (0 = all)")
    return p.parse_args()


def main():
    args = parse_args()

    if os.path.exists(PID_PATH) and not args.force:
        logger.error(f"PID file exists ({PID_PATH}); another run may be active. Use --force to override.")
        print(f"Already running? Remove {PID_PATH} or use --force.")
        return

    with open(PID_PATH, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

    try:
        utr = UTRScraper()
        matcher = PlayerMatcher(utr)
        targets = load_targets()
        if args.limit:
            targets = targets[:args.limit]

        logger.info("=" * 70)
        logger.info(f"Started re-check: {len(targets)} player(s) with 0.00 UTR, "
                    f"interval {args.interval}s, dry_run={args.dry_run}")
        if not targets:
            logger.info("Nothing to do.")
            return

        stats = {"recovered": 0, "remapped": 0, "still-zero": 0}
        for i, t in enumerate(targets, 1):
            _wait_if_rate_limited()
            logger.info(f"--- [{i}/{len(targets)}] ---")
            try:
                outcome = check_one(utr, matcher, t, dry_run=args.dry_run)
                stats[outcome] = stats.get(outcome, 0) + 1
            except Exception as e:
                logger.exception(f"  error checking USTA {t.get('usta_id')}: {e}")
            if i < len(targets):
                time.sleep(args.interval)

        logger.info(f"Finished. recovered={stats['recovered']} remapped={stats['remapped']} "
                    f"still-zero={stats['still-zero']}")
    finally:
        try:
            os.remove(PID_PATH)
        except OSError:
            pass


if __name__ == "__main__":
    main()
