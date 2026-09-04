import re
import json
import logging
import requests
from typing import Optional
from scripts.config import UTR_USER, UTR_PASS, PLAYWRIGHT_USER_DIR
from scripts.db import save_utr_player_profile

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.utrsports.net/v2/search/players"
PROFILE_URL = "https://api.utrsports.net/v1/player/{utr_id}/profile"
SESSION_URL = "https://app.utrsports.net"


class UTRScraper:
    def __init__(self, email=UTR_USER, password=UTR_PASS, jwt=None):
        self.email = email
        self.password = password
        self.session = requests.Session()
        self.auth_token = None
        self._no_cred_warned = False
        self._jwt_cache = jwt
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })

    def login_if_needed(self) -> bool:
        if not self.email or not self.password:
            if not self._no_cred_warned:
                logger.warning("No UTR credentials provided.")
                self._no_cred_warned = True
            return False
        if self.auth_token:
            return True

        logger.info("Logging into UTR API...")
        login_payload = {"email": self.email, "password": self.password}
        try:
            resp = self.session.post(
                f"{SESSION_URL}/api/auth/login",
                json=login_payload, timeout=15
            )
            if resp.status_code == 200:
                data = resp.json()
                self.auth_token = data.get("accessToken") or data.get("token")
                if self.auth_token:
                    self.session.headers["Authorization"] = f"Bearer {self.auth_token}"
                    logger.info("UTR login successful.")
                    return True
            logger.warning(f"UTR login failed: {resp.status_code}")
            return False
        except Exception as e:
            logger.warning(f"UTR login error: {e}")
            return False

    def search_players(self, name: str, city: str = None, state: str = None) -> list:
        logger.info(f"Searching UTR for: {name}")

        candidates = self._api_search(name, city=city, state=state)
        if candidates:
            return candidates

        parts = name.split()
        if len(parts) > 2:
            broad = f"{parts[0]} {parts[-1]}"
            logger.info(f"Broadening search: {broad}")
            candidates = self._api_search(broad, city=city, state=state)

        return candidates or []

    def search_and_enrich(self, name: str, city: str = None, state: str = None) -> list:
        candidates = self.search_players(name, city=city, state=state)
        for c in candidates:
            utr_s = c.get("utr_singles")
            utr_d = c.get("utr_doubles")
            if utr_s is None or utr_s == 0.0:
                profile = self.get_player_profile(c["utr_id"])
                if profile:
                    c["utr_singles"] = profile.get("utr_singles")
                    c["utr_doubles"] = profile.get("utr_doubles")
                    c["name"] = profile.get("name") or c["name"]
        return candidates

    def _ensure_jwt(self):
        if not self._jwt_cache:
            self._jwt_cache = self._get_jwt()
        return self._jwt_cache

    def get_player_profile(self, utr_id: str, fallback_no_jwt=True) -> Optional[dict]:
        import time
        jwt = self._ensure_jwt()

        def _fetch(use_jwt):
            headers = {}
            if use_jwt and jwt:
                headers["Cookie"] = f"jwt={jwt}"
            return self.session.get(PROFILE_URL.format(utr_id=utr_id), headers=headers, timeout=10)

        # Try with JWT first (precise ratings), fall back to no-JWT (integer-rounded)
        resp = _fetch(use_jwt=True)
        using_fallback = False
        if resp.status_code == 429 and fallback_no_jwt:
            logger.warning(f"Rate limited (429) on {utr_id}, falling back to no-JWT (approximate rating)")
            resp = _fetch(use_jwt=False)
            using_fallback = True

        if resp.status_code != 200:
            logger.warning(f"UTR profile returned {resp.status_code} for {utr_id}")
            return None

        try:
            data = resp.json()
            loc = data.get("location") or {}
            result = {
                "utr_id": utr_id,
                "name": data.get("displayName"),
                "city": loc.get("cityName"),
                "state": loc.get("stateAbbr") or loc.get("stateName"),
                "utr_singles": data.get("singlesUtr"),
                "utr_doubles": data.get("doublesUtr"),
                "total_matches": data.get("totalResults"),
                "nationality": data.get("locationNationality"),
            }
            save_utr_player_profile(
                utr_id=utr_id, name=result["name"],
                city=result["city"], state=result["state"],
                utr_singles=result["utr_singles"],
                utr_doubles=result["utr_doubles"],
            )
            tag = " (no-JWT fallback)" if using_fallback else ""
            logger.info(f"UTR profile: {result['name']} S={result['utr_singles']} D={result['utr_doubles']}{tag}")
            return result
        except Exception as e:
            logger.warning(f"UTR profile parse error: {e}")
            return None

    def _api_search(self, name: str, city: str = None, state: str = None) -> list:
        params = {"query": name, "top": 10}
        try:
            resp = self.session.get(SEARCH_URL, params=params, timeout=10)
            if resp.status_code != 200:
                return []

            data = resp.json()
            hits = data.get("hits", [])
            candidates = []

            for hit in hits:
                src = hit.get("source", {})
                pid = src.get("id")
                if pid is None:
                    continue

                display = src.get("displayName") or f"{src.get('firstName', '')} {src.get('lastName', '')}".strip()
                if not display:
                    continue

                loc = src.get("location") or {}
                city = loc.get("cityName") or (loc.get("display", "").split(",")[0].strip() if loc.get("display") else None)
                state = loc.get("stateName")

                singles = src.get("singlesUtr")
                doubles = src.get("doublesUtr")

                candidates.append({
                    "utr_id": str(pid),
                    "name": display,
                    "city": city,
                    "state": state,
                    "utr_singles": float(singles) if singles is not None else None,
                    "utr_doubles": float(doubles) if doubles is not None else None
                })

            logger.info(f"UTR API search found {len(candidates)} candidates.")
            return candidates
        except Exception as e:
            logger.debug(f"UTR API search error: {e}")
            return []

    def _get_jwt(self) -> Optional[str]:
        """Get JWT token from the persistent Playwright context cookie jar."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return None
        try:
            with sync_playwright() as p:
                with p.chromium.launch_persistent_context(
                    PLAYWRIGHT_USER_DIR, headless=True
                ) as context:
                    cookies = context.cookies("https://api.utrsports.net")
                    for c in cookies:
                        if c["name"] == "jwt":
                            return c["value"]
        except Exception as e:
            logger.debug(f"JWT extraction error: {e}")
        return None

    def get_player_matches(self, utr_id: str) -> list:
        jwt = self._ensure_jwt()
        if not jwt:
            logger.warning("No JWT available for UTR match history API.")
            return []

        logger.info(f"Fetching match history for UTR ID: {utr_id}")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Cookie": f"jwt={jwt}",
        }
        try:
            resp = requests.get(
                f"https://api.utrsports.net/v4/player/{utr_id}/results?type=s",
                headers=headers, timeout=15
            )
            if resp.status_code != 200:
                logger.debug(f"UTR v4 results returned {resp.status_code}")
                return []

            data = resp.json()
            events = data.get("events", [])
            opponents = []

            for ev in events:
                match_results = ev.get("results", [])
                if not match_results:
                    for draw in ev.get("draws", []):
                        match_results.extend(draw.get("results", []))
                for res in match_results:
                    players = res.get("players", {})
                    for side_key, player in players.items():
                        if not player or not player.get("id"):
                            continue
                        pid = str(player["id"])
                        if pid == utr_id:
                            continue
                        opponents.append({
                            "opponent_utr_id": pid,
                            "opponent_name": f"{player.get('lastName', '')}, {player.get('firstName', '')}".strip(", "),
                            "opponent_first": player.get("firstName", ""),
                            "opponent_last": player.get("lastName", ""),
                            "singles_utr": player.get("singlesUtr"),
                            "gender": player.get("gender"),
                            "side": side_key,
                        })

            logger.info(f"Found {len(opponents)} opponent entries for UTR ID {utr_id}")
            seen = set()
            unique = []
            for o in opponents:
                if o["opponent_utr_id"] not in seen:
                    seen.add(o["opponent_utr_id"])
                    unique.append(o)
            return unique
        except Exception as e:
            logger.debug(f"Match history error: {e}")
            return []


if __name__ == "__main__":
    scraper = UTRScraper()
    res = scraper.search_players("Robert Jordan")
    print(f"Found {len(res)} results:")
    for r in res[:5]:
        print(f"  {r['name']:30s} | {r.get('city',''):15s} {r.get('state',''):2s} | UTR S: {r.get('utr_singles')} | ID: {r['utr_id']}")
