"""Refresh integer UTR ratings (>= --min-rating) to precise decimals.

Many cached ratings are integer-valued (no-JWT fallback), e.g. 5.00. This job
re-fetches one profile per interval (default 60s) so UTR returns the precise rating
(e.g. 5.17). The cache is updated by UTRScraper.get_player_profile itself.

Run (from repo root):
    python -m scripts.maintenance.refresh_high_utr [--interval 60] [--min-rating 5] [--limit N]
"""
import os
import time
import argparse
import logging

from scripts.service import db
from scripts.service.utr_scraper import UTRScraper
from scripts.service.usta_scraper import USTAScraper

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG_PATH = os.path.join(BASE_DIR, "refresh_high_utr.log")
PID_PATH = os.path.join(BASE_DIR, "refresh_high_utr.pid")

logger = logging.getLogger("refresh_high_utr")
logger.setLevel(logging.INFO)
_fh = logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_fh)


def load_targets(min_rating):
    with db.get_connection() as conn:
        rows = conn.execute(
            """
            SELECT utr_id, name, utr_singles
            FROM utr_player_profiles
            WHERE utr_singles IS NOT NULL
              AND utr_singles >= ?
              AND utr_singles = ROUND(utr_singles)
            ORDER BY utr_singles DESC, name
            """,
            (float(min_rating),),
        ).fetchall()
    return [dict(r) for r in rows]


def _wait_if_rate_limited():
    if UTRScraper.is_rate_limited():
        wait = UTRScraper.rate_limit_remaining() + 5
        logger.warning(f"UTR rate limit active; sleeping {wait}s")
        time.sleep(wait)


def _usta_id_for(utr_id):
    with db.get_connection() as conn:
        row = conn.execute(
            """SELECT usta_id FROM player_mappings WHERE utr_id = ?
               ORDER BY CASE match_method WHEN 'manual_correct' THEN 0 ELSE 1 END LIMIT 1""",
            (utr_id,),
        ).fetchone()
    return row["usta_id"] if row else None


def _old_wtn(usta_id):
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT wtn_singles FROM usta_player_profiles WHERE usta_id = ?", (usta_id,)
        ).fetchone()
    return row["wtn_singles"] if row else None


def parse_args():
    p = argparse.ArgumentParser(description="Refresh integer UTR ratings to precise decimals.")
    p.add_argument("--interval", type=int, default=60,
                   help="Seconds between players (default 60 = one per minute)")
    p.add_argument("--min-rating", type=float, default=5.0,
                   help="Only refresh integer ratings >= this value (default 5.0)")
    p.add_argument("--limit", type=int, default=0, help="Process at most N players (0 = all)")
    p.add_argument("--dry-run", action="store_true", help="List targets only; do not fetch")
    p.add_argument("--force", action="store_true", help="Start even if a PID file exists")
    return p.parse_args()


def main():
    args = parse_args()

    if os.path.exists(PID_PATH) and not args.force:
        logger.error(f"PID file exists ({PID_PATH}); use --force to override.")
        print(f"Already running? Remove {PID_PATH} or use --force.")
        return

    with open(PID_PATH, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

    try:
        targets = load_targets(args.min_rating)
        if args.limit:
            targets = targets[:args.limit]

        logger.info("=" * 70)
        logger.info(f"Started high-UTR refresh: {len(targets)} integer profile(s) "
                    f">= {args.min_rating:.2f}, interval {args.interval}s")

        if args.dry_run:
            for t in targets:
                logger.info(f"  target: {t['name']} ({t['utr_id']}) = {t['utr_singles']}")
            logger.info("Dry run complete.")
            return

        utr = UTRScraper()
        usta = USTAScraper()
        updated = unchanged = failed = 0
        wtn_updated = wtn_same = wtn_skipped = 0

        for i, t in enumerate(targets, 1):
            _wait_if_rate_limited()
            old = t["utr_singles"]
            logger.info(f"--- [{i}/{len(targets)}] {t['name']} ({t['utr_id']}) current UTR {old} ---")

            # ── UTR precise rating ──
            try:
                prof = utr.get_player_profile(t["utr_id"])
            except Exception as e:
                logger.exception(f"  UTR error: {e}")
                prof = None

            if not prof:
                if utr.last_profile_not_found:
                    logger.warning("  UTR profile no longer exists (404)")
                else:
                    logger.warning("  UTR profile fetch failed/empty")
                failed += 1
            else:
                new = prof.get("utr_singles")
                if new is None:
                    logger.warning("  UTR: no rating returned")
                    failed += 1
                elif float(new) != float(old):
                    logger.info(f"  UTR updated: {old} -> {new}")
                    updated += 1
                else:
                    logger.info(f"  UTR unchanged (still {new})")
                    unchanged += 1

            # ── USTA WTN (latest) ──
            usta_id = _usta_id_for(t["utr_id"])
            if not usta_id:
                logger.info("  WTN skipped (no USTA mapping)")
                wtn_skipped += 1
            else:
                old_wtn = _old_wtn(usta_id)
                try:
                    uprof = usta.fetch_player_profile(usta_id, headless=True)
                except Exception as e:
                    logger.warning(f"  WTN fetch error for USTA {usta_id}: {e}")
                    uprof = None
                if uprof is None:
                    logger.warning(f"  WTN fetch failed for USTA {usta_id}")
                else:
                    new_wtn = uprof.get("wtn_singles")
                    logger.info(f"  WTN: {old_wtn} -> {new_wtn}")
                    if new_wtn is not None and new_wtn != old_wtn:
                        wtn_updated += 1
                    else:
                        wtn_same += 1

            if i < len(targets):
                time.sleep(args.interval)

        logger.info(
            f"Finished. UTR updated={updated} unchanged={unchanged} failed={failed}; "
            f"WTN updated={wtn_updated} unchanged={wtn_same} skipped={wtn_skipped}"
        )
    finally:
        try:
            os.remove(PID_PATH)
        except OSError:
            pass


if __name__ == "__main__":
    main()
