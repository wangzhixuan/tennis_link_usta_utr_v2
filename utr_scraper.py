import re
import json
import logging
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from config import HEADLESS, UTR_USER, UTR_PASS
from db import save_utr_cache

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class UTRScraper:
    def __init__(self, email=UTR_USER, password=UTR_PASS, headless=HEADLESS):
        self.email = email
        self.password = password
        self.headless = headless
        self.session = requests.Session()
        self.auth_token = None
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*"
        }

    def login_and_capture_session(self) -> bool:
        """
        Launches Playwright, logs into UTR, and intercepts requests to capture JWT or cookies.
        """
        if not self.email or not self.password:
            logger.warning("No UTR credentials provided. Scrapes will run in unauthenticated public mode.")
            return False

        logger.info("Starting Playwright login to UTR...")
        
        login_url = "https://app.utrsports.net/login"
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless, args=["--disable-blink-features=AutomationControlled"])
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            # Intercept headers to find Bearer authorization
            def handle_request(request):
                headers = request.headers
                if "authorization" in headers and headers["authorization"].startswith("Bearer "):
                    self.auth_token = headers["authorization"]
                    logger.info("Successfully intercepted UTR Bearer Token!")

            page.on("request", handle_request)

            try:
                page.goto(login_url, wait_until="networkidle", timeout=60000)
                
                # Check if we are already logged in or need to type credentials
                # Let's wait for login form
                page.wait_for_selector("input[type='email']", timeout=15000)
                page.fill("input[type='email']", self.email)
                page.fill("input[type='password']", self.password)
                
                # Click login button
                # Buttons could be submit or have specific class names or contain 'Log In' text
                login_btn = page.query_selector("button[type='submit']") or page.query_selector("button:has-text('Log In')") or page.query_selector("button:has-text('Sign In')")
                if login_btn:
                    login_btn.click()
                else:
                    page.keyboard.press("Enter")

                # Wait for navigation and network settle (JWT is usually requested now)
                page.wait_for_timeout(7000)
                
                # Verify login success by checking URL or elements
                current_url = page.url
                if "login" in current_url:
                    logger.warning("UTR Login might have failed. Current page is still login page.")
                    # Take screenshot to help debug
                    page.screenshot(path="utr_login_fail.png")
                else:
                    logger.info("UTR Login appears successful.")

                # Capture cookies for requests session
                cookies = context.cookies()
                for cookie in cookies:
                    self.session.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"])
                
                if self.auth_token:
                    self.headers["Authorization"] = self.auth_token
                    return True
                
                return len(cookies) > 0

            except Exception as e:
                logger.error(f"Error during UTR Playwright login: {e}")
                try:
                    page.screenshot(path="utr_error_screenshot.png")
                except Exception:
                    pass
                return False
            finally:
                browser.close()

    def search_players(self, name: str) -> list:
        """
        Searches players using UTR API. Falls back to public API if not logged in.
        Returns a list of candidate dictionary objects.
        """
        logger.info(f"Searching UTR for: {name}")
        
        # Possible API search endpoints
        # Endpoint 1: https://api.utrsports.net/v2/search/players
        # Endpoint 2: https://api.universaltennis.com/v2/search/players
        search_urls = [
            "https://api.utrsports.net/v2/search/players",
            "https://api.universaltennis.com/v2/search/players",
            "https://api.utrsports.net/v1/search/players",
            "https://api.universaltennis.com/v1/search/players"
        ]
        
        params = {
            "query": name,
            "skip": 0,
            "limit": 10,
            "top": 10
        }

        # Try API calls using the session
        for url in search_urls:
            try:
                response = self.session.get(url, headers=self.headers, params=params, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    candidates = []
                    
                    # Parse different structures of response
                    results = data.get("results") or data.get("players") or data.get("items")
                    if not results and isinstance(data, list):
                        results = data
                    
                    if results:
                        for item in results:
                            source = item.get("source", {}) if isinstance(item.get("source"), dict) else item
                            
                            utr_id = source.get("id") or source.get("player_id") or source.get("idCode") or item.get("id")
                            p_name = source.get("name") or source.get("fullName") or item.get("name")
                            
                            if not utr_id or not p_name:
                                continue
                            
                            # Location
                            loc = source.get("location", {})
                            city, state = None, None
                            if isinstance(loc, dict):
                                city = loc.get("city") or loc.get("cityName")
                                state = loc.get("state") or loc.get("stateCode") or loc.get("display")
                            elif isinstance(loc, str):
                                parts = loc.split(",")
                                city = parts[0].strip()
                                if len(parts) > 1:
                                    state = parts[1].strip()
                            
                            # If no city/state, try fallback fields
                            if not city:
                                city = source.get("city") or source.get("cityName") or source.get("addressCity")
                            if not state:
                                state = source.get("state") or source.get("stateCode") or source.get("addressState")

                            # Ratings
                            utr_s = source.get("singlesUtr") or source.get("singles_utr") or source.get("utr") or source.get("ratingSingles")
                            utr_d = source.get("doublesUtr") or source.get("doubles_utr") or source.get("ratingDoubles")
                            
                            # Handle dictionary style ratings
                            if isinstance(utr_s, dict):
                                utr_s = utr_s.get("rating") or utr_s.get("utr")
                            if isinstance(utr_d, dict):
                                utr_d = utr_d.get("rating") or utr_d.get("utr")

                            candidates.append({
                                "utr_id": str(utr_id),
                                "name": p_name,
                                "city": city,
                                "state": state,
                                "utr_singles": float(utr_s) if utr_s else None,
                                "utr_doubles": float(utr_d) if utr_d else None
                            })
                        
                        return candidates
            except Exception as e:
                logger.debug(f"Search API error on {url}: {e}")

        # Fallback public scrape using Playwright if API didn't work
        return self._fallback_playwright_search(name)

    def get_player_matches(self, utr_id: str) -> list:
        """
        Gets a list of historical matches for verification heuristics.
        Returns a list of match dicts: {"opponent_name": str, "date": str, "score": str, "win": bool}
        """
        logger.info(f"Fetching match history for UTR ID: {utr_id}")
        
        # UTR matches endpoint
        urls = [
            f"https://api.utrsports.net/v1/player/{utr_id}/results",
            f"https://api.universaltennis.com/v1/player/{utr_id}/results",
            f"https://api.utrsports.net/v2/player/{utr_id}/results"
        ]
        
        for url in urls:
            try:
                response = self.session.get(url, headers=self.headers, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    matches = []
                    results = data.get("results") or data.get("items") or data.get("data")
                    if not results and isinstance(data, list):
                        results = data
                        
                    if results:
                        for r in results:
                            # Parse match info
                            opponent = r.get("opponent", {}) or r.get("opponentPlayer", {})
                            opp_name = opponent.get("name") or opponent.get("fullName") or opponent.get("displayName")
                            
                            date = r.get("date") or r.get("matchDate") or r.get("dateString")
                            score = r.get("score") or r.get("scoreString")
                            outcome = r.get("outcome") or r.get("result") # 'WIN' or 'LOSS'
                            win = outcome == "WIN" or r.get("win", True)
                            
                            if opp_name:
                                matches.append({
                                    "opponent_name": opp_name,
                                    "date": date,
                                    "score": score,
                                    "win": win
                                })
                        return matches
            except Exception as e:
                logger.debug(f"Match history API error on {url}: {e}")

        # Fallback to empty list
        return []

    def _fallback_playwright_search(self, name: str) -> list:
        """
        Fallback Playwright scraping for when requests API calls fail or are blocked.
        """
        logger.info(f"Running fallback Playwright search for: {name}")
        search_url = f"https://app.utrsports.net/search?query={requests.utils.quote(name)}"
        
        candidates = []
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=self.headless)
                context = browser.new_context()
                page = context.new_page()
                page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(4000) # Wait for page to render

                # Try parsing the player results
                html = page.content()
                soup = BeautifulSoup(html, "html.parser")
                
                # Look for player profile anchors
                profile_anchors = soup.find_all("a", href=True)
                for anchor in profile_anchors:
                    href = anchor["href"]
                    if "/profiles/" in href:
                        match = re.search(r"/profiles/(\d+)", href)
                        if match:
                            utr_id = match.group(1)
                            p_name = anchor.get_text().strip()
                            if not p_name or len(p_name) < 3:
                                continue
                                
                            # Parse parent card for city/state and rating
                            city, state = None, None
                            utr_s = None
                            
                            parent = anchor
                            for _ in range(4):
                                parent = parent.parent
                                if not parent: break
                                text = parent.get_text()
                                
                                # Location match
                                loc_match = re.search(r"\b([A-Za-z\s]+),\s*([A-Z]{2})\b", text)
                                if loc_match:
                                    city = loc_match.group(1).strip()
                                    state = loc_match.group(2).strip()
                                    
                                # Rating match (e.g., "10.24" or "UTR 9.5")
                                rating_match = re.search(r"(?:utr)?\s*([1-9]\d*(?:\.\d+)?)\b", text, re.IGNORECASE)
                                if rating_match:
                                    try:
                                        utr_s = float(rating_match.group(1))
                                    except ValueError:
                                        pass
                            
                            candidates.append({
                                "utr_id": utr_id,
                                "name": p_name,
                                "city": city,
                                "state": state,
                                "utr_singles": utr_s,
                                "utr_doubles": None
                            })
                
                # Deduplicate candidates
                seen = set()
                dedup = []
                for c in candidates:
                    if c["utr_id"] not in seen:
                        seen.add(c["utr_id"])
                        dedup.append(c)
                return dedup

            except Exception as e:
                logger.error(f"Fallback Playwright search failed: {e}")
                return []
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

if __name__ == "__main__":
    # Test execution
    scraper = UTRScraper()
    # Execute login and print result
    success = scraper.login_and_capture_session()
    print(f"Login success: {success}")
    if success:
        res = scraper.search_players("Federer")
        print(f"Found {len(res)} results:")
        for r in res[:3]:
            print(r)
