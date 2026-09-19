"""Bootstrap the USTA<->UTR mapping DB from a single known pair.

A fresh clone has an empty ``player_mappings`` table. Give the tool one pair you
already know (a player's USTA ID and UTR ID). It then compares that player's match
history on both sides -- matching the SAME match by date + score + opponent name/
residence -- to discover new, high-confidence USTA<->UTR pairs, and repeats over a
couple of expansion layers until it has built a small starter DB (~20-30 players).

Both the USTA and UTR sessions must be logged in first.

CLI usage (manual testing on a scratch DB):
    python -m scripts.bootstrap --usta 2018671404 --utr 3639763 \
        --db %TEMP%\\bootstrap_test.db --reset
"""
import os
import sys
import time
import argparse
import logging

from scripts.config import (
    BOOTSTRAP_MAX_PLAYERS,
    BOOTSTRAP_MAX_DEPTH,
    DB_PATH as DEFAULT_DB_PATH,
)
from scripts import db
from scripts.db import save_mapping, save_usta_player_profile, save_utr_player_profile
from scripts.usta_scraper import USTAScraper
from scripts.utr_scraper import UTRScraper
from scripts.matcher import PlayerMatcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _noop(msg):
    pass


def bootstrap_from_seed(
    usta_id: str,
    utr_id: str,
    max_players: int = None,
    max_depth: int = None,
    progress_cb=None,
    usta: USTAScraper = None,
    utr: UTRScraper = None,
    matcher: PlayerMatcher = None,
) -> dict:
    """
    Seed the mapping DB from one known (usta_id, utr_id) pair and expand via match histories.

    Returns {ok, added, players, depth, error}.
    """
    max_players = int(max_players or BOOTSTRAP_MAX_PLAYERS)
    max_depth = int(max_depth or BOOTSTRAP_MAX_DEPTH)
    log = progress_cb or _noop

    usta = usta or USTAScraper()
    utr = utr or UTRScraper()
    matcher = matcher or PlayerMatcher(utr)

    usta_id, utr_id = str(usta_id), str(utr_id)

    # ── 1. Validate the seed pair on both sides ──
    log(f"Validating seed: USTA {usta_id} <-> UTR {utr_id} ...")
    try:
        usta_prof = usta.fetch_player_profile(usta_id) or {}
    except Exception as e:
        usta_prof = {}
        log(f"  USTA profile lookup failed: {e}")
    try:
        utr_prof = utr.get_player_profile(utr_id) or {}
    except Exception as e:
        utr_prof = {}
        log(f"  UTR profile lookup failed: {e}")

    if not usta_prof or not utr_prof:
        return {"ok": False, "added": 0, "players": [], "depth": 0,
                "error": "Seed not found on USTA and/or UTR (check IDs / login)."}

    name_sim = matcher.name_similarity(usta_prof.get("name", ""), utr_prof.get("name", ""))
    if name_sim < 0.6:
        return {"ok": False, "added": 0, "players": [], "depth": 0,
                "error": (f"Seed names don't match (similarity={name_sim:.2f}): "
                          f"{usta_prof.get('name')} vs {utr_prof.get('name')}")}

    save_mapping(usta_id, utr_id, match_method="manual_seed", confidence=1.0)
    save_usta_player_profile(
        usta_id=usta_id, name=usta_prof.get("name"), city=usta_prof.get("city"),
        state=usta_prof.get("state"), section=usta_prof.get("section"),
        district=usta_prof.get("district"), gender=usta_prof.get("gender"),
        age_category=usta_prof.get("age_category"), ball_color=usta_prof.get("ball_color"),
        competition_level=usta_prof.get("competition_level"),
        itf_tennis_id=usta_prof.get("itf_tennis_id"), nationality=usta_prof.get("nationality"),
        wtn_singles=usta_prof.get("wtn_singles"), wtn_doubles=usta_prof.get("wtn_doubles"),
        rankings=usta_prof.get("rankings"),
    )
    log(f"Seed saved: {usta_prof.get('name')} (USTA {usta_id} <-> UTR {utr_id})")

    usta_profile_by_id = {usta_id: usta_prof}
    utr_profile_by_id = {utr_id: utr_prof}
    known_usta = {usta_id}
    known_utr = {utr_id}
    players = [{"usta_id": usta_id, "utr_id": utr_id, "name": usta_prof.get("name"),
                "confidence": 1.0, "match_method": "manual_seed", "profile": usta_prof}]

    def _enrich_profiles(pairs):
        """Fetch opponent profiles to corroborate residence (cached)."""
        for p in pairs:
            uid, tid = p["usta_id"], p["utr_id"]
            log(f"    corroborating USTA {uid} <-> UTR {tid} ...")
            if uid not in usta_profile_by_id:
                try:
                    usta_profile_by_id[uid] = usta.fetch_player_profile(uid) or {}
                except Exception:
                    usta_profile_by_id[uid] = {}
            if tid not in utr_profile_by_id:
                try:
                    utr_profile_by_id[tid] = utr.get_player_profile(tid) or {}
                except Exception:
                    utr_profile_by_id[tid] = {}

    # ── 2. BFS expansion ──
    frontier = [(usta_id, utr_id)]
    depth = 0
    log(f"Target: up to {max_players} players (seed included), {max_depth} layer(s).")
    while frontier and len(known_usta) < max_players and depth < max_depth:
        depth += 1
        log(f"Expansion layer {depth}: {len(frontier)} player(s); "
            f"{len(known_usta)} mapped so far.")
        next_frontier = []
        for (uid, tid) in frontier:
            remaining = max_players - len(known_usta)
            if remaining <= 0:
                break
            log(f"  Fetching match history: USTA {uid} <-> UTR {tid} ...")
            try:
                usta_matches = usta.get_player_matches(uid)
                utr_matches = utr.get_player_match_history(tid)
            except Exception as e:
                log(f"  ! failed to fetch history for USTA {uid} / UTR {tid}: {e}")
                continue
            if not usta_matches or not utr_matches:
                continue

            # Pass 1: date+score candidates (ignore name for now, so residence can
            # later corroborate weak-name matches). Skip already-known players and
            # only keep as many as we still need, so we don't over-fetch.
            prelim = matcher.pair_from_match_histories(
                usta_matches, utr_matches,
                min_confidence=0.0, require_name_match=False,
            )
            prelim = [p for p in prelim
                      if p["usta_id"] not in known_usta and p["utr_id"] not in known_utr]
            if not prelim:
                continue
            prelim.sort(key=lambda x: x["confidence"], reverse=True)
            prelim = prelim[:remaining]

            # Pass 2: corroborate residence, then re-score
            _enrich_profiles(prelim)
            confirmed = matcher.pair_from_match_histories(
                usta_matches, utr_matches,
                usta_profile_by_id=usta_profile_by_id,
                utr_profile_by_id=utr_profile_by_id,
            )
            for p in confirmed:
                if p["usta_id"] in known_usta or p["utr_id"] in known_utr:
                    continue
                if len(known_usta) >= max_players:
                    break
                save_mapping(p["usta_id"], p["utr_id"],
                             match_method=p["match_method"], confidence=p["confidence"])
                known_usta.add(p["usta_id"])
                known_utr.add(p["utr_id"])
                next_frontier.append((p["usta_id"], p["utr_id"]))
                players.append({**p, "profile": usta_profile_by_id.get(p["usta_id"], {})})
                log(f"  + {p['name']}: USTA {p['usta_id']} <-> UTR {p['utr_id']} "
                    f"(conf {p['confidence']})")
        frontier = next_frontier
        if frontier and depth < max_depth:
            time.sleep(0.5)

    log(f"Bootstrap complete: {len(known_usta)} players mapped over {depth} layer(s).")
    return {"ok": True, "added": len(known_usta), "players": players,
            "depth": depth, "error": None}


def _guard_reset(db_path: str, force: bool):
    if not force and os.path.abspath(db_path) == os.path.abspath(DEFAULT_DB_PATH):
        raise SystemExit(
            f"Refusing to --reset the default DB ({db_path}). "
            f"Pass a scratch --db path, or --force if you really mean it."
        )


def prepare_db(db_path: str = None, reset: bool = False, force: bool = False) -> str:
    """
    Point the app at `db_path` (if given), ensure the schema exists, and optionally
    wipe all rows. Must be called before any profile/mapping reads or writes when a
    custom --db is used, since init_db() otherwise only ran against the default DB.
    """
    if db_path:
        db.DB_PATH = os.path.abspath(db_path)
    if reset:
        _guard_reset(db.DB_PATH, force)
    db.init_db()
    if reset:
        db.reset_all()
    return db.DB_PATH


def parse_args():
    p = argparse.ArgumentParser(description="Bootstrap the USTA<->UTR mapping DB from one known pair.")
    p.add_argument("--usta", required=True, help="Known player's USTA ID")
    p.add_argument("--utr", required=True, help="Known player's UTR ID")
    p.add_argument("--db", default=None, help="Override DB path (e.g. a scratch DB)")
    p.add_argument("--reset", action="store_true", help="Wipe all rows in the target DB first")
    p.add_argument("--force", action="store_true", help="Allow --reset on the default DB")
    p.add_argument("--max-players", type=int, default=BOOTSTRAP_MAX_PLAYERS)
    p.add_argument("--max-depth", type=int, default=BOOTSTRAP_MAX_DEPTH)
    return p.parse_args()


def main():
    args = parse_args()

    target_db = prepare_db(args.db, reset=args.reset, force=args.force)
    print(f"Using DB: {target_db}")
    if args.reset:
        print(f"Reset DB: {target_db}")

    usta = USTAScraper()
    utr = UTRScraper()

    print("Checking logins (both USTA and UTR are required)...")
    if not usta.check_usta_token():
        print("ERROR: Not logged in to USTA. Log in via the app, then retry.")
        sys.exit(2)
    if not utr._ensure_jwt():
        print("ERROR: Not logged in to UTR (no JWT). Log in via the app, then retry.")
        sys.exit(2)

    result = bootstrap_from_seed(
        args.usta, args.utr,
        max_players=args.max_players, max_depth=args.max_depth,
        progress_cb=lambda m: print(m),
        usta=usta, utr=utr,
    )

    if not result["ok"]:
        print(f"Bootstrap failed: {result['error']}")
        sys.exit(1)
    print(f"\nDone: {result['added']} players mapped over {result['depth']} layer(s).")


if __name__ == "__main__":
    main()
