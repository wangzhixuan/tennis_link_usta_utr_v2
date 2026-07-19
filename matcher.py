import difflib
import logging
from db import get_mapping, save_mapping, save_utr_cache
from utr_scraper import UTRScraper

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class PlayerMatcher:
    def __init__(self, utr_scraper: UTRScraper):
        self.utr_scraper = utr_scraper

    def normalize_name(self, name: str) -> str:
        """
        Normalizes a name for comparison by converting to lowercase,
        removing punctuation/middle initials, and stripping extra space.
        """
        if not name:
            return ""
        name = name.lower()
        # Remove middle initials/names if they are formatted like "First M. Last"
        parts = name.split()
        if len(parts) > 2:
            # If the middle part is a single letter (with or without dot), remove it
            if len(parts[1]) <= 2 or parts[1].endswith("."):
                parts.pop(1)
        return " ".join(parts).strip()

    def name_similarity(self, name1: str, name2: str) -> float:
        """
        Calculates similarity ratio between two names.
        """
        n1 = self.normalize_name(name1)
        n2 = self.normalize_name(name2)
        return difflib.SequenceMatcher(None, n1, n2).ratio()

    def location_score(self, city1, state1, city2, state2) -> float:
        """
        Returns a score from 0.0 to 1.0 representing location matching.
        """
        if not state1 or not state2:
            return 0.5  # Neutral if one is missing
        
        s1 = state1.strip().upper()
        s2 = state2.strip().upper()
        
        # State abbreviation mapping helper
        # Sometimes UTR lists Full State, sometimes abbreviation. Let's do a simple substring check or exact check.
        state_match = (s1 == s2) or (s1 in s2) or (s2 in s1)
        
        if not state_match:
            # Different states - lower confidence unless cities are empty
            return 0.1 if city1 or city2 else 0.3
            
        if not city1 or not city2:
            # Same state, but city is missing
            return 0.8
            
        c1 = city1.strip().lower()
        c2 = city2.strip().lower()
        
        if c1 == c2:
            return 1.0
            
        # Check for partial match/nicknames (e.g. "St. Petersburg" vs "Saint Petersburg")
        city_sim = difflib.SequenceMatcher(None, c1, c2).ratio()
        if city_sim > 0.8:
            return 0.95
        
        return 0.75  # Same state, different cities

    def find_utr_profile(self, usta_player: dict, tournament_players: list = None) -> dict:
        """
        Finds the matching UTR profile for a given USTA player dict.
        
        Parameters:
        - usta_player: dict containing 'name', 'usta_id', 'city', 'state', etc.
        - tournament_players: list of dicts of other players in this tournament
                             (used for circle of competition heuristics)
        """
        usta_id = usta_player["usta_id"]
        usta_name = usta_player["name"]
        hometown_city = usta_player.get("city")
        hometown_state = usta_player.get("state")
        
        # Level 1: Check Local Cache Map
        cached_mapping = get_mapping(usta_id)
        if cached_mapping:
            logger.info(f"Cache HIT for USTA ID {usta_id} -> UTR ID {cached_mapping['utr_id']}")
            return {
                "utr_id": cached_mapping["utr_id"],
                "confidence": cached_mapping["confidence"],
                "match_method": "cached",
                "source": "local_db"
            }

        # Level 2: Search UTR and Apply Name & Location matching
        candidates = self.utr_scraper.search_players(usta_name)
        if not candidates:
            # Try a broader search by splitting name if we have middle name/hyphen
            parts = usta_name.split()
            if len(parts) > 2:
                broad_name = f"{parts[0]} {parts[-1]}"
                logger.info(f"No exact name results. Trying broad search for: {broad_name}")
                candidates = self.utr_scraper.search_players(broad_name)
        
        if not candidates:
            logger.warning(f"No UTR candidates found for player: {usta_name}")
            return None

        scored_candidates = []
        for cand in candidates:
            # 1. Name match score
            name_sim = self.name_similarity(usta_name, cand["name"])
            if name_sim < 0.7:
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
        if len(scored_candidates) > 1 or (scored_candidates and scored_candidates[0]["confidence"] < 0.9):
            if tournament_players:
                # Compile other players' names in the tournament for comparison
                other_player_names = {self.normalize_name(p["name"]) for p in tournament_players if p["usta_id"] != usta_id}
                
                logger.info(f"Running Match History Circle heuristic for {usta_name} against {len(scored_candidates)} candidates...")
                for item in scored_candidates:
                    cand = item["candidate"]
                    matches = self.utr_scraper.get_player_matches(cand["utr_id"])
                    
                    circle_matches = 0
                    for m in matches:
                        m_opp_normalized = self.normalize_name(m["opponent_name"])
                        # If this opponent is in the list of tournament players, it's a huge circle match signal!
                        if m_opp_normalized in other_player_names:
                            circle_matches += 1
                            logger.info(f"Circle MATCH! {usta_name}'s UTR candidate {cand['name']} (ID {cand['utr_id']}) played tournament participant '{m['opponent_name']}'")
                    
                    if circle_matches > 0:
                        # Massive boost to confidence
                        item["confidence"] = min(0.99, item["confidence"] + 0.35 + (0.05 * circle_matches))
                        item["match_method"] = "match_history_circle"
                        logger.info(f"Boosted confidence to {item['confidence']:.2f} for {cand['name']}")

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
