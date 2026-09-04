import os
import sys
import gc
import time
import random
import logging
import argparse
import pandas as pd
from playwright.sync_api import sync_playwright

from scripts.config import DB_PATH, GOLDEN_MAPPING_PATH
from scripts.db import get_usta_player_profile, get_utr_player_profile
from scripts.usta_scraper import USTAScraper
from scripts.utr_scraper import UTRScraper

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "batch_fetch.log")
_fh = logging.FileHandler(LOG_FILE, mode="w")
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
root = logging.getLogger()
root.setLevel(logging.INFO)
root.addHandler(_fh)
logger = logging.getLogger(__name__)

SLEEP_MIN = 3
SLEEP_MAX = 6


def load_mappings():
    df = pd.read_csv(GOLDEN_MAPPING_PATH)
    pairs = list(zip(df["usta_id"].astype(str), df["utr_id"].astype(str)))
    return pairs


def needs_usta_fetch(usta_id):
    cached = get_usta_player_profile(usta_id)
    if cached is None:
        return True
    if cached.get("wtn_singles") is None and cached.get("wtn_singles_confidence") is not None:
        return True
    if not cached.get("section"):
        return True
    return False


def needs_utr_fetch(utr_id):
    cached = get_utr_player_profile(utr_id)
    if cached is None:
        return True
    rating = cached.get("utr_singles")
    if rating is None or rating == 0.0:
        return True
    # TODO: Restore JWT re-fetch check (rating == int(rating)) once rate limits clear
    return False


def fetch_pairs(pairs, do_usta, do_utr, headless=True):
    usta_scraper = USTAScraper()
    total = len(pairs)
    usta_fetched = 0
    usta_skipped = 0
    utr_fetched = 0
    utr_skipped = 0

    # Extract UTR JWT before starting any Playwright browser (to avoid nested Playwright conflicts)
    utr_jwt = None
    if do_utr:
        try:
            temp = UTRScraper()
            utr_jwt = temp._get_jwt()
            logger.info(f"UTR JWT: {'obtained' if utr_jwt else 'NOT FOUND - ratings will be obfuscated'}")
        except Exception as e:
            logger.warning(f"JWT extraction failed: {e}")
    utr_scraper = UTRScraper(jwt=utr_jwt)

    playwright = None
    browser = None
    if do_usta:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=headless)

    try:
        for idx, (usta_id, utr_id) in enumerate(pairs):
            did_fetch = False
            if do_usta:
                if needs_usta_fetch(usta_id):
                    logger.info(f"[{idx+1}/{total}] USTA {usta_id}...")
                    try:
                        profile = usta_scraper.fetch_player_profile(usta_id, headless=headless, browser=browser)
                        if profile:
                            usta_fetched += 1
                            did_fetch = True
                        else:
                            logger.warning(f"  Empty USTA profile for {usta_id}")
                    except Exception as e:
                        logger.error(f"  Error fetching USTA {usta_id}: {e}")
                else:
                    usta_skipped += 1

            if do_utr:
                if needs_utr_fetch(utr_id):
                    logger.info(f"[{idx+1}/{total}] UTR {utr_id}...")
                    try:
                        profile = utr_scraper.get_player_profile(utr_id)
                        if profile:
                            utr_fetched += 1
                            did_fetch = True
                        else:
                            logger.warning(f"  Empty UTR profile for {utr_id}")
                    except Exception as e:
                        logger.error(f"  Error fetching UTR {utr_id}: {e}")
                else:
                    utr_skipped += 1

            if did_fetch and idx + 1 < total:
                delay = random.uniform(SLEEP_MIN, SLEEP_MAX)
                logger.info(f"  Sleep {delay:.1f}s...")
                time.sleep(delay)
    finally:
        if browser:
            browser.close()
        if playwright:
            playwright.stop()
        gc.collect()

    logger.info(f"USTA: {usta_fetched} fetched, {usta_skipped} skipped")
    logger.info(f"UTR: {utr_fetched} fetched, {utr_skipped} skipped")


def parse_args():
    parser = argparse.ArgumentParser(description="Batch-fetch USTA/UTR profiles for golden mapping players.")
    parser.add_argument("--usta", action="store_true", help="Fetch USTA profiles")
    parser.add_argument("--utr", action="store_true", help="Fetch UTR profiles")
    parser.add_argument("--visible", action="store_true",
                        help="Show Playwright browser window (default: headless)")
    return parser.parse_args()


def main():
    args = parse_args()
    headless = not args.visible
    logger.info("Loading golden mapping...")
    pairs = load_mappings()
    logger.info(f"Loaded {len(pairs)} golden mapping pairs")

    do_usta = args.usta
    do_utr = args.utr

    if do_usta:
        usta_total = sum(1 for u, _ in pairs if needs_usta_fetch(u))
        usta_skip = sum(1 for u, _ in pairs if not needs_usta_fetch(u))
        logger.info(f"USTA: {usta_total} to fetch, {usta_skip} already cached")
    if do_utr:
        utr_total = sum(1 for _, r in pairs if needs_utr_fetch(r))
        utr_skip = sum(1 for _, r in pairs if not needs_utr_fetch(r))
        logger.info(f"UTR: {utr_total} to fetch, {utr_skip} already cached")

    fetch_pairs(pairs, do_usta=do_usta, do_utr=do_utr, headless=headless)
    logger.info("Done.")


if __name__ == "__main__":
    main()
