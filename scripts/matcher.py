import re
import os
import csv
import difflib
import logging
from typing import Optional
from scripts.config import GOLDEN_MAPPING_PATH
from scripts.db import get_mapping, save_mapping, save_utr_cache, get_utr_player_profile
from scripts.utr_scraper import UTRScraper

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

STATE_ABBREV = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY"
}

NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


class PlayerMatcher:
    def __init__(self, utr_scraper: UTRScraper):
        self.utr_scraper = utr_scraper

    @staticmethod
    def load_golden_mapping() -> tuple[dict, dict]:
        """Load golden mapping CSV into lookup dicts: utr_by_usta and usta_by_utr."""
        utr_by_usta = {}
        usta_by_utr = {}
        path = GOLDEN_MAPPING_PATH
        if not os.path.exists(path):
            logger.debug(f"Golden mapping file not found: {path}")
            return utr_by_usta, usta_by_utr
        try:
            with open(path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    usta_id = row.get("usta_id", "").strip()
                    utr_id = row.get("utr_id", "").strip()
                    if usta_id and utr_id:
                        utr_by_usta[usta_id] = utr_id
                        usta_by_utr[utr_id] = usta_id
            logger.info(f"Loaded {len(utr_by_usta)} golden mappings.")
        except Exception as e:
            logger.warning(f"Failed to load golden mapping: {e}")
        return utr_by_usta, usta_by_utr

    @staticmethod
    def _abbrev_state(state: str) -> str:
        s = state.strip().lower()
        if len(s) == 2:
            return s.upper()
        return STATE_ABBREV.get(s, s.upper())

    def normalize_name(self, name: str) -> str:
        if not name:
            return ""
        name = re.sub(r"[.,;:!?'\"]+", "", name).lower().strip()
        parts = name.split()
        parts = [p for p in parts if p not in NAME_SUFFIXES]
        if len(parts) > 2 and (len(parts[1]) <= 2 or parts[1].endswith(".")):
            parts.pop(1)
        return " ".join(parts).strip()

    def name_similarity(self, name1: str, name2: str) -> float:
        n1 = self.normalize_name(name1)
        n2 = self.normalize_name(name2)
        score = difflib.SequenceMatcher(None, n1, n2).ratio()
        # Also try reversed word order (handles "Last First" vs "First Last")
        parts = n1.split()
        if len(parts) == 2:
            rev = f"{parts[1]} {parts[0]}"
            score = max(score, difflib.SequenceMatcher(None, rev, n2).ratio())
        return score

    def location_score(self, city1, state1, city2, state2) -> float:
        if not state1 or not state2:
            return 0.5

        s1 = self._abbrev_state(state1)
        s2 = self._abbrev_state(state2)
        state_match = s1 == s2

        if not state_match:
            return 0.1 if city1 or city2 else 0.3

        if not city1 or not city2:
            return 0.8

        c1 = city1.strip().lower()
        c2 = city2.strip().lower()

        if c1 == c2:
            return 1.0

        city_sim = difflib.SequenceMatcher(None, c1, c2).ratio()
        if city_sim > 0.8:
            return 0.95

        return 0.75

    def find_utr_profile(
        self,
        usta_player: dict,
        tournament_players: list = None,
        no_cross_ref: bool = False,
        ignore_cache: bool = False,
        exclude_utr_ids: set = None,
        validate_cached: bool = False,
    ) -> Optional[dict]:
        """
        Finds the matching UTR profile for a given USTA player dict.
        
        Parameters:
        - usta_player: dict containing 'name', 'usta_id', 'city', 'state', etc.
        - tournament_players: list of dicts of other players in this tournament
                             (used for circle of competition heuristics)
        - no_cross_ref: whether to skip tournament opponent cross-referencing
        - ignore_cache: if True, skips local database cache to force re-search
        - exclude_utr_ids: set of UTR IDs to exclude (e.g. dead/changed 404 IDs)
        - validate_cached: if True, verifies cached UTR ID still exists on UTR Sports
        """
        usta_id = usta_player["usta_id"]
        usta_name = usta_player["name"]
        hometown_city = usta_player.get("city")
        hometown_state = usta_player.get("state")
        
        excluded = {str(x) for x in exclude_utr_ids} if exclude_utr_ids else set()

        # Level 1: Check Local Cache Map
        if not ignore_cache:
            cached_mapping = get_mapping(usta_id)
            if cached_mapping and str(cached_mapping.get("utr_id")) not in excluded:
                utr_id = str(cached_mapping["utr_id"])
                # If validation requested, verify that the cached profile still exists
                if validate_cached and not self.utr_scraper.check_profile_exists(utr_id):
                    logger.warning(
                        f"Cached UTR ID {utr_id} for USTA ID {usta_id} no longer exists on UTR Sports (404)! "
                        f"Bypassing stale mapping to search for new profile..."
                    )
                    excluded.add(utr_id)
                else:
                    logger.info(f"Cache HIT for USTA ID {usta_id} -> UTR ID {utr_id}")
                    cached_profile = get_utr_player_profile(utr_id)
                    return {
                        "utr_id": utr_id,
                        "confidence": cached_mapping["confidence"],
                        "match_method": "cached",
                        "source": "local_db",
                        "name": cached_profile.get("name") if cached_profile else None,
                        "city": cached_profile.get("city") if cached_profile else None,
                        "state": cached_profile.get("state") if cached_profile else None,
                        "utr_singles": cached_profile.get("utr_singles") if cached_profile else None,
                        "utr_doubles": cached_profile.get("utr_doubles") if cached_profile else None,
                    }

        # Level 2: Search UTR and Apply Name & Location matching
        candidates = self.utr_scraper.search_players(usta_name, city=hometown_city, state=hometown_state)
        if not candidates:
            # Try a broader search by splitting name if we have middle name/hyphen
            parts = usta_name.split()
            if len(parts) > 2:
                broad_name = f"{parts[0]} {parts[-1]}"
                logger.info(f"No exact name results. Trying broad search for: {broad_name}")
                candidates = self.utr_scraper.search_players(broad_name, city=hometown_city, state=hometown_state)
        
        if excluded and candidates:
            candidates = [c for c in candidates if str(c.get("utr_id")) not in excluded]

        if not candidates:
            logger.warning(f"No UTR candidates found for player: {usta_name}")
            return None

        scored_candidates = []
        for cand in candidates:
            if str(cand.get("utr_id")) in excluded:
                continue

            # 1. Name match score
            name_sim = self.name_similarity(usta_name, cand["name"])
            if name_sim < 0.6:
                # Name is too different, skip this candidate
                continue
                
            # 2. Location match score
            loc_score = self.location_score(
                hometown_city, hometown_state,
                cand.get("city"), cand.get("state")
            )
            
            # Base confidence combines name similarity and location score
            confidence = (name_sim * 0.6) + (loc_score * 0.4)
            
            scored_candidates.append({
                "candidate": cand,
                "base_confidence": confidence,
                "confidence": confidence,
                "match_method": "name_geo"
            })

        if not scored_candidates:
            logger.warning(f"No qualified candidates (by name similarity) for {usta_name}")
            return None

        # Level 3: Tournament Circle Heuristic (Deep Match History Verification)
        # Only run if we have candidates and have tournament_players to compare against
        if no_cross_ref:
            logger.info(f"Skipping cross-reference matching for {usta_name} (--no-cross-ref)")
        elif len(scored_candidates) > 1 or (scored_candidates and scored_candidates[0]["confidence"] < 0.9):
            if tournament_players:
                # Compile other players' names and USTA IDs in the tournament
                other_names = {self.normalize_name(p["name"]) for p in tournament_players if p["usta_id"] != usta_id}
                other_usta_ids = {p["usta_id"] for p in tournament_players if p["usta_id"] != usta_id}
                
                # Load golden mapping for known UTR_ID -> USTA_ID cross-reference
                utr_by_usta, usta_by_utr = self.load_golden_mapping()
                
                logger.info(f"Running Match History Circle heuristic for {usta_name} against {len(scored_candidates)} candidates...")
                for item in scored_candidates:
                    cand = item["candidate"]
                    matches = self.utr_scraper.get_player_matches(cand["utr_id"])
                    
                    circle_matches = 0
                    cross_ref_matches = 0
                    
                    for m in matches:
                        m_opp_normalized = self.normalize_name(m["opponent_name"])
                        opp_utr_id = m.get("opponent_utr_id")
                        
                        # Method A: Opponent name matches a tournament participant
                        if m_opp_normalized in other_names:
                            circle_matches += 1
                            logger.info(f"Circle match: {usta_name}'s candidate {cand['name']} played '{m['opponent_name']}' (in tournament)")
                        
                        # Method B: Opponent UTR ID is in golden mapping -> known USTA ID
                        # Check if that USTA ID is also in this tournament
                        if opp_utr_id and opp_utr_id in usta_by_utr:
                            known_usta_id = usta_by_utr[opp_utr_id]
                            if known_usta_id in other_usta_ids:
                                cross_ref_matches += 1
                                logger.info(f"Cross-ref MATCH! Opponent UTR {opp_utr_id} (USTA {known_usta_id}) is in same tournament!")
                    
                    total_boost = 0
                    if circle_matches > 0:
                        total_boost += 0.35 + (0.05 * circle_matches)
                    if cross_ref_matches > 0:
                        total_boost += 0.40 + (0.05 * cross_ref_matches)
                    
                    if total_boost > 0:
                        item["confidence"] = min(0.99, item["confidence"] + total_boost)
                        item["match_method"] = "match_history_circle"
                        logger.info(f"Boosted confidence to {item['confidence']:.2f} for {cand['name']} (circle={circle_matches}, xref={cross_ref_matches})")

        # Sort candidates by confidence score descending
        scored_candidates.sort(key=lambda x: x["confidence"], reverse=True)
        best_match = scored_candidates[0]
        
        # Save mapping if confidence is high enough (e.g., > 0.75)
        # If it's a very high match, mark as highly trusted.
        if best_match["confidence"] >= 0.75:
            cand = best_match["candidate"]
            save_mapping(
                usta_id=usta_id,
                utr_id=cand["utr_id"],
                match_method=best_match["match_method"],
                confidence=best_match["confidence"]
            )
            # Also update/save details to cache
            save_utr_cache(
                utr_id=cand["utr_id"],
                name=cand["name"],
                city=cand.get("city"),
                state=cand.get("state"),
                utr_singles=cand.get("utr_singles"),
                utr_doubles=cand.get("utr_doubles")
            )
            logger.info(f"Saved high-confidence match ({best_match['confidence']:.2f}): {usta_name} -> UTR ID {cand['utr_id']}")

        return {
            "utr_id": best_match["candidate"]["utr_id"],
            "name": best_match["candidate"]["name"],
            "city": best_match["candidate"].get("city"),
            "state": best_match["candidate"].get("state"),
            "utr_singles": best_match["candidate"].get("utr_singles"),
            "utr_doubles": best_match["candidate"].get("utr_doubles"),
            "confidence": best_match["confidence"],
            "match_method": best_match["match_method"],
            "source": "search"
        }

    def rematch_player(
        self,
        usta_player: dict,
        tournament_players: list = None,
        no_cross_ref: bool = False,
        exclude_utr_ids: set = None,
    ) -> Optional[dict]:
        """
        Forces a fresh search for a player's UTR profile, ignoring any cached mapping.
        Used when the previously mapped UTR ID no longer exists (e.g. merged, changed, or deleted).
        Excludes known dead/changed UTR IDs from candidate results.
        If a qualified match is found, updates player_mappings with the new UTR ID.
        """
        usta_id = usta_player.get("usta_id")
        excluded = set(str(x) for x in exclude_utr_ids) if exclude_utr_ids else set()
        old_mapping = get_mapping(usta_id) if usta_id else None
        if old_mapping and old_mapping.get("utr_id"):
            excluded.add(str(old_mapping["utr_id"]))

        logger.info(
            f"Auto re-matching player '{usta_player.get('name')}' (USTA ID: {usta_id}, "
            f"excluding dead UTR IDs: {excluded})..."
        )

        match = self.find_utr_profile(
            usta_player=usta_player,
            tournament_players=tournament_players,
            no_cross_ref=no_cross_ref,
            ignore_cache=True,
            exclude_utr_ids=excluded,
        )

        if match:
            logger.info(
                f"Successfully re-matched '{usta_player.get('name')}': new UTR ID {match['utr_id']} "
                f"(confidence={match['confidence']:.2f}, method={match['match_method']})"
            )
        else:
            logger.warning(f"Could not find a replacement UTR profile for '{usta_player.get('name')}'.")

        return match
