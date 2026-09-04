import os
import re
import argparse
import logging
import pandas as pd

from scripts.config import UTR_USER, UTR_PASS
from scripts.usta_scraper import USTAScraper
from scripts.utr_scraper import UTRScraper
from scripts.matcher import PlayerMatcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tournaments")


def parse_args():
    parser = argparse.ArgumentParser(
        description="TennisLink: Fetch USTA players, match them with UTR ratings, and export a TSV report."
    )
    parser.add_argument("tournament", help="USTA tournament GUID or full URL.")
    parser.add_argument("-d", "--division", default=None,
                        help="Target division or event name (e.g., 'Boys 18', 'Girls').")
    parser.add_argument("--no-login", action="store_true",
                        help="Skip UTR login (search-only, no match history).")
    parser.add_argument("--no-profiles", action="store_true",
                        help="Skip fetching detailed USTA profiles and rankings.")
    parser.add_argument("--visible", action="store_true",
                        help="Run Playwright browser in visible mode.")
    parser.add_argument("--no-precise-utr", action="store_true",
                        help="Skip scraping precise decimal UTR from UTR Sports website.")
    parser.add_argument("--no-cross-ref", action="store_true",
                        help="Skip cross-reference matching via UTR match history + golden mapping.")
    return parser.parse_args()


def build_ranking_list_name(division: str) -> str:
    """Convert 'Girls U12' -> 'Girls' 12 National Standings List (combined)'"""
    if not division:
        return None
    parts = division.split()
    gender = None
    age = None
    for p in parts:
        pl = p.lower()
        if pl in ("girls", "boys"):
            gender = pl.capitalize()
        elif pl.isdigit():
            age = pl
        elif pl.lower().startswith("u") and pl[1:].isdigit():
            age = pl[1:]
    if gender and age:
        return f"{gender}' {age} National Standings List (combined)"
    return None


def build_output_path(tournament_info: dict, division: str) -> str:
    name = tournament_info["name"]
    guid = tournament_info["guid"][:8]
    date_raw = tournament_info.get("start_date", "")
    yyyymmdd = date_raw[:10].replace("-", "") if date_raw else "unknown"
    safe_name = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_")
    safe_div = re.sub(r"[^a-zA-Z0-9]+", "_", division).strip("_") if division else "All"
    fname = f"Tournament.{yyyymmdd}.{safe_name}.{guid}.{safe_div}.tsv"
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, fname)


def get_points_for_list(rankings: list, list_name: str):
    """Return points from the specified ranking list, or None."""
    if not list_name:
        return None
    lower_target = list_name.lower()
    for r in rankings:
        if r.get("display_label", "").lower() == lower_target:
            return r.get("points")
    return None


def main():
    args = parse_args()

    usta_scraper = USTAScraper()
    logger.info(f"Fetching tournament: '{args.tournament}' [Division: {args.division or 'All'}]")
    tournament_info = usta_scraper.get_tournament_info(args.tournament)

    headless = not args.visible
    run_profiles = not args.no_profiles
    run_precise_utr = not args.no_precise_utr

    if run_profiles:
        logger.info("Detailed profile fetching enabled (may take ~10s per player)...")
        usta_players = usta_scraper.fetch_profiles_for_tournament(
            args.tournament, args.division, headless=headless
        )
    else:
        usta_players = usta_scraper.scrape_tournament(args.tournament, args.division, headless=headless)

    if not usta_players:
        logger.error("No players extracted from the USTA tournament.")
        return

    logger.info(f"Retrieved {len(usta_players)} players from USTA.")

    utr_scraper = UTRScraper()
    if not args.no_login:
        utr_scraper.login_if_needed()

    matcher = PlayerMatcher(utr_scraper)

    ranking_list_name = build_ranking_list_name(args.division)
    if ranking_list_name:
        logger.info(f"Ranking points column: '{ranking_list_name}'")
    else:
        logger.info("No ranking points column (could not parse division).")

    resolved_records = []
    logger.info("Resolving player UTR ratings and mapping profiles...")
    for idx, up in enumerate(usta_players):
        logger.info(f"[{idx+1}/{len(usta_players)}] Resolving player: {up['name']}")
        match_res = matcher.find_utr_profile(up, usta_players, no_cross_ref=args.no_cross_ref)

        def _val(v, default="N/A"):
            return v if v is not None else default

        points = get_points_for_list(up.get("rankings", []), ranking_list_name)

        record = {
            "Player Name": up["name"],
            "Hometown": f"{up.get('city') or ''}, {up.get('state') or ''}".strip(", "),
            "USTA ID": up["usta_id"],
            "WTN Singles": _val(up.get("wtn_singles")),
            "WTN Doubles": _val(up.get("wtn_doubles")),
            "Ranking Points": _val(points),
            "Ranking List": _val(ranking_list_name),
        }

        if match_res:
            record.update({
                "UTR ID": match_res["utr_id"],
                "Match Confidence": match_res["confidence"],
                "Match Method": match_res["match_method"],
                "UTR Singles": _val(match_res.get("utr_singles")),
                "UTR Doubles": _val(match_res.get("utr_doubles")),
            })
        else:
            record.update({
                "UTR ID": "N/A",
                "Match Confidence": 0.0,
                "Match Method": "None",
                "UTR Singles": "N/A",
                "UTR Doubles": "N/A",
            })

        resolved_records.append(record)

    if run_precise_utr:
        matched_ids = [
            r["UTR ID"] for r in resolved_records
            if r["UTR ID"] not in ("N/A", None)
        ]
        if matched_ids:
            logger.info(f"Fetching precise UTR for {len(matched_ids)} players via Playwright...")
            precise_results = utr_scraper.fetch_precise_utr_batch(matched_ids, headless=False)
            for record in resolved_records:
                uid = record["UTR ID"]
                if uid in precise_results:
                    pr = precise_results[uid]
                    if pr["utr_singles"] is not None:
                        record["UTR Singles"] = pr["utr_singles"]
                    if pr["utr_doubles"] is not None:
                        record["UTR Doubles"] = pr["utr_doubles"]
                    logger.info(f"  {record['Player Name']}: precise UTR S={pr['utr_singles']} D={pr['utr_doubles']}")
        else:
            logger.info("No matched UTR IDs to scrape.")

    df = pd.DataFrame(resolved_records)
    col_order = [
        "Player Name", "Hometown", "USTA ID",
        "WTN Singles", "WTN Doubles",
        "Ranking Points", "Ranking List",
        "UTR ID", "Match Confidence", "UTR Singles", "UTR Doubles", "Match Method",
    ]
    df = df[col_order]

    output_file = build_output_path(tournament_info, args.division)
    logger.info(f"Saving compiled data to {output_file}...")
    df.to_csv(output_file, index=False, sep="\t")
    logger.info(f"Done! Report: {output_file}")


if __name__ == "__main__":
    main()
