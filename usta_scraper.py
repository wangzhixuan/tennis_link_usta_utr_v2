import re
import json
import logging
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from config import HEADLESS
from db import save_usta_cache

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class USTAScraper:
    def __init__(self, headless=HEADLESS):
        self.headless = headless

    def scrape_tournament(self, url_or_id: str, target_division: str = None):
        """
        Scrapes a USTA tournament page for players in a target division.
        Supports full URLs or tournament IDs (TIDs).
        """
        url = url_or_id
        if not url.startswith("http"):
            # Assume it's a tournament ID (TID)
            # USTA tournament URLs can be formatted as:
            url = f"https://playtennis.usta.com/competitions/tournament/{url_or_id}/players"
        elif not url.endswith("/players") and "players" not in url:
            # Ensure we are going to the players tab if possible
            if "/overview" in url:
                url = url.replace("/overview", "/players")
            elif "/draws" in url:
                url = url.replace("/draws", "/players")
            else:
                # Append players if it's a standard competition url
                url = url.rstrip("/") + "/players"

        logger.info(f"Navigating to USTA tournament page: {url}")
        
        captured_json_data = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless, args=["--disable-blink-features=AutomationControlled"])
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            # Intercept network responses to catch API payloads
            def handle_response(response):
                try:
                    if "api" in response.url or "players" in response.url or "competition" in response.url:
                        content_type = response.headers.get("content-type", "")
                        if "json" in content_type:
                            data = response.json()
                            captured_json_data.append((response.url, data))
                except Exception:
                    pass

            page.on("response", handle_response)
            
            try:
                # Go to page
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(5000)  # Wait for dynamic content to load
                
                # Check if we need to click a specific division / event dropdown
                # Modern USTA tournament pages often have a dropdown to filter by division/event.
                # Let's try to find and click a dropdown if target_division is specified.
                self._select_division_if_needed(page, target_division)
                
                # Let's get the page source
                html = page.content()
                players = self._parse_players_html(html, target_division)
                
                # If we captured JSON API payloads, let's try to extract from them as they are cleaner
                api_players = self._parse_from_captured_json(captured_json_data, target_division)
                if api_players:
                    logger.info(f"Successfully extracted {len(api_players)} players from captured API responses.")
                    players.update(api_players)

                # Save players to database cache
                for usta_id, p_info in players.items():
                    save_usta_cache(
                        usta_id=usta_id,
                        name=p_info["name"],
                        city=p_info.get("city"),
                        state=p_info.get("state"),
                        wtn_singles=p_info.get("wtn_singles"),
                        wtn_doubles=p_info.get("wtn_doubles"),
                        usta_ranking=p_info.get("ranking")
                    )

                return list(players.values())

            except Exception as e:
                logger.error(f"Error scraping USTA: {e}")
                # Save page screenshot for debugging in case of failure
                try:
                    page.screenshot(path="usta_error_screenshot.png")
                    logger.info("Saved usta_error_screenshot.png for debugging.")
                except Exception:
                    pass
                return []
            finally:
                browser.close()

    def _select_division_if_needed(self, page, target_division):
        if not target_division:
            return
        
        logger.info(f"Attempting to select division/event: {target_division}")
        try:
            # Look for select dropdowns or tab buttons
            # Let's try standard selectors for dropdowns
            dropdowns = page.query_selector_all("select")
            for select in dropdowns:
                options = select.query_selector_all("option")
                for option in options:
                    text = option.inner_text().strip().lower()
                    if target_division.lower() in text:
                        val = option.get_attribute("value")
                        select.select_option(value=val)
                        page.wait_for_timeout(2000)
                        logger.info(f"Selected option '{option.inner_text().strip()}' in dropdown.")
                        return
            
            # If no select, try looking for buttons or tabs that match the division
            buttons = page.query_selector_all("button, a")
            for btn in buttons:
                text = btn.inner_text().strip().lower()
                if target_division.lower() in text:
                    btn.click()
                    page.wait_for_timeout(2000)
                    logger.info(f"Clicked element containing '{target_division}'.")
                    return
        except Exception as e:
            logger.warning(f"Could not select division via interactive UI: {e}")

    def _parse_players_html(self, html: str, target_division: str = None):
        """
        Parses players list from HTML page.
        """
        soup = BeautifulSoup(html, "html.parser")
        players = {}

        # 1. Look for anchor tags with player profile links
        # USTA profiles often point to usta.com/en/home/play/player-profile.html#/?playerQuery=<usta_id> 
        # or playtennis.usta.com/players/<usta_id> or playtennis.usta.com/Player/<usta_id>
        profile_links = soup.find_all("a", href=True)
        
        for link in profile_links:
            href = link["href"]
            usta_id = None
            
            # Match usta id in different URL styles
            if "playerQuery=" in href:
                match = re.search(r"playerQuery=(\d+)", href)
                if match:
                    usta_id = match.group(1)
            elif "ustaId=" in href:
                match = re.search(r"ustaId=(\d+)", href)
                if match:
                    usta_id = match.group(1)
            elif "/players/" in href or "/Player/" in href:
                match = re.search(r"/players/([\w\d\-]+)", href, re.IGNORECASE) or re.search(r"/Player/([\w\d\-]+)", href, re.IGNORECASE)
                if match:
                    usta_id = match.group(1)
            
            if usta_id:
                name = link.get_text().strip()
                if not name or len(name) < 3 or any(x in name.lower() for x in ["profile", "view", "details"]):
                    continue
                
                # Check if this player card/row contains location & ratings
                # Traverse up to find container (like <tr> or player card <div>)
                city, state = None, None
                wtn_s, wtn_d = None, None
                rank = None
                
                # Let's walk up parent elements to find structured data
                parent = link
                for _ in range(5):
                    parent = parent.parent
                    if not parent:
                        break
                    
                    text_content = parent.get_text()
                    
                    # 1. Match City, ST (e.g., "Austin, TX" or "Miami, FL")
                    location_match = re.search(r"\b([A-Za-z\s]+),\s*([A-Z]{2})\b", text_content)
                    if location_match:
                        city = location_match.group(1).strip()
                        state = location_match.group(2).strip()
                    
                    # 2. Match WTN (e.g. WTN 16.4 or Singles WTN: 12.3)
                    wtn_matches = re.findall(r"(?:wtn|world tennis number)\s*:?\s*(\d+\.?\d*)", text_content, re.IGNORECASE)
                    if wtn_matches:
                        try:
                            wtn_s = float(wtn_matches[0])
                            if len(wtn_matches) > 1:
                                wtn_d = float(wtn_matches[1])
                        except ValueError:
                            pass
                    
                    # 3. Match ranking (e.g. Rank: 42 or Ranking: #42)
                    rank_match = re.search(r"(?:rank|ranking|#)\s*:?\s*#?(\d+)", text_content, re.IGNORECASE)
                    if rank_match:
                        try:
                            rank = int(rank_match.group(1))
                        except ValueError:
                            pass
                
                # Deduplicate and store
                if usta_id not in players:
                    players[usta_id] = {
                        "usta_id": usta_id,
                        "name": name,
                        "city": city,
                        "state": state,
                        "wtn_singles": wtn_s,
                        "wtn_doubles": wtn_d,
                        "ranking": rank
                    }
                else:
                    # Merge information if found more details
                    p = players[usta_id]
                    if city: p["city"] = city
                    if state: p["state"] = state
                    if wtn_s: p["wtn_singles"] = wtn_s
                    if wtn_d: p["wtn_doubles"] = wtn_d
                    if rank: p["ranking"] = rank

        # Fallback / manual row parsing
        # If no profiles matched, let's parse tables
        if not players:
            # Look for table rows
            for row in soup.find_all("tr"):
                cells = [c.get_text().strip() for c in row.find_all(["td", "th"])]
                if len(cells) >= 2:
                    # Let's see if first or second cell has a name
                    # and try to extract player from it
                    pass

        return players

    def _parse_from_captured_json(self, captured_json, target_division):
        """
        Parses players out of intercepted network JSON responses.
        USTA ClubSpark uses several JSON payloads for events & players.
        """
        players = {}
        for url, data in captured_json:
            try:
                # Look for player arrays
                # ClubSpark JSON responses often contain objects with "players", "entrants", "registrations", "competitors"
                found_list = None
                if isinstance(data, dict):
                    # BFS/DFS to find lists with player objects
                    found_list = self._search_dict_for_players(data)
                elif isinstance(data, list):
                    found_list = data
                
                if found_list:
                    for item in found_list:
                        if not isinstance(item, dict):
                            continue
                        # Standardize attributes
                        name = item.get("name") or item.get("fullName") or item.get("displayName")
                        # Handle cases where name is split
                        if not name and "firstName" in item:
                            name = f"{item.get('firstName', '')} {item.get('lastName', '')}".strip()
                        
                        usta_id = item.get("ustaId") or item.get("id") or item.get("memberId") or item.get("playerCode")
                        if not name or not usta_id:
                            continue
                        
                        # Convert to string
                        usta_id = str(usta_id)
                        
                        # Exclude organizational / non-player IDs if they aren't numbers
                        if not usta_id.isdigit():
                            # Maybe check if it's in a profile link
                            profile_url = item.get("profileUrl") or ""
                            match = re.search(r"\d+", profile_url)
                            if match:
                                usta_id = match.group(0)
                            else:
                                continue
                        
                        city = item.get("city") or item.get("town") or item.get("hometown")
                        state = item.get("state") or item.get("region")
                        
                        # WTN
                        wtn_s = item.get("wtnSingles") or item.get("wtn_singles") or item.get("wtnRating")
                        wtn_d = item.get("wtnDoubles") or item.get("wtn_doubles")
                        
                        # Rank
                        rank = item.get("rank") or item.get("ranking")
                        
                        players[usta_id] = {
                            "usta_id": usta_id,
                            "name": name,
                            "city": city,
                            "state": state,
                            "wtn_singles": float(wtn_s) if wtn_s else None,
                            "wtn_doubles": float(wtn_d) if wtn_d else None,
                            "ranking": int(rank) if rank else None
                        }
            except Exception as e:
                logger.debug(f"Error parsing JSON item: {e}")
                
        return players

    def _search_dict_for_players(self, d: dict):
        # Recursively search for list of dictionary items containing keys like 'ustaId', 'wtn' or 'player'
        for k, v in d.items():
            if k in ["players", "entrants", "registrations", "competitors", "candidates", "results"] and isinstance(v, list):
                return v
            if isinstance(v, dict):
                res = self._search_dict_for_players(v)
                if res:
                    return res
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        res = self._search_dict_for_players(item)
                        if res:
                            return res
        return None

if __name__ == "__main__":
    # Test execution
    scraper = USTAScraper(headless=True)
    # Testing with a dummy ID or placeholder
    res = scraper.scrape_tournament("25-12345", "Boys")
    print(f"Scraped {len(res)} players.")
