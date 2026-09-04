import re
import json
import logging
import requests
from typing import Optional
from playwright.sync_api import sync_playwright
from scripts.db import save_usta_player_profile
from scripts.utr_scraper import PLAYWRIGHT_USER_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

PARTICIPANT_ENDPOINT = "https://prd-usta-kube-tournamentdesk-public-api.clubspark.pro/"
TOURNAMENT_ENDPOINT = "https://prd-usta-kube-tournaments.clubspark.pro/"
PROFILE_URL = "https://www.usta.com/en/home/play/player-search/profile.html#?uaid={usta_id}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Origin": "https://playtennis.usta.com",
    "Referer": "https://playtennis.usta.com/",
    "Content-Type": "application/json"
}

VENUE_GET_PLAYERS_QUERY = """
query GetPlayers($id: UUID!, $queryParameters: QueryParametersPaged!) {
  paginatedPublicTournamentRegistrations(
    tournamentId: $id
    queryParameters: $queryParameters
  ) {
    totalItems
    items {
      firstName: playerFirstName
      gender: playerGender
      lastName: playerLastName
      city: playerCity
      state: playerState
      playerName
      playerId { key value }
      playerCustomIds { key value }
      events {
        id
        division {
          gender
          ageCategory { todsCode maximumAge type }
          eventType
          familyType
        }
      }
    }
  }
}
"""

PARTICIPANT_QUERY = """
query getTournamentParticipants($tournamentId: ID!) {
  getTournamentParticipants(tournamentId: $tournamentId) {
    participantId
    participantName
    participantRole
    participantStatus
    person {
      addresses { city state }
      personId
      personOtherIds { personId uniqueOrganisationName }
      standardGivenName
      standardFamilyName
    }
    events { eventId entryStage }
  }
}
"""

TOURNAMENT_QUERY = """
query GetTournament($id: UUID!, $previewMode: Boolean) {
  publishedTournament(id: $id, previewMode: $previewMode) {
    id
    name
    identificationCode
    timings { startDate endDate }
    publishedEvents(previewMode: $previewMode) {
      id
      division {
        ageCategory { type minimumAge maximumAge }
        eventType
        gender
        ratingCategory { ratingCategoryType ratingType value }
      }
      formatConfiguration { drawSize eventFormat }
    }
  }
}
"""


class USTAScraper:
    def __init__(self):
        self.session = requests.Session()

    @staticmethod
    def extract_guid(url_or_id: str) -> str:
        guid_pattern = re.compile(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
        )
        match = guid_pattern.search(url_or_id)
        if match:
            return match.group(0).upper()
        if re.match(r"^[0-9a-fA-F-]{36}$", url_or_id.strip()):
            return url_or_id.strip().upper()
        raise ValueError(
            f"Could not extract tournament GUID from '{url_or_id}'. "
            "Provide a full USTA tournament URL or the GUID directly."
        )

    def fetch_participants(self, tournament_guid: str) -> list:
        payload = {
            "operationName": "getTournamentParticipants",
            "variables": {"tournamentId": tournament_guid},
            "query": PARTICIPANT_QUERY
        }
        resp = self.session.post(PARTICIPANT_ENDPOINT, json=payload, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        participants = data.get("data", {}).get("getTournamentParticipants") or []
        logger.info(f"Fetched {len(participants)} participants from tournament API.")
        return participants

    def fetch_events(self, tournament_guid: str) -> list:
        payload = {
            "operationName": "GetTournament",
            "variables": {"id": tournament_guid.lower(), "previewMode": False},
            "query": TOURNAMENT_QUERY
        }
        resp = self.session.post(TOURNAMENT_ENDPOINT, json=payload, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        events = data.get("data", {}).get("publishedTournament", {}).get("publishedEvents", [])
        logger.info(f"Fetched {len(events)} events from tournament API.")
        return events

    def fetch_venue_players(self, tournament_guid: str, headless: bool = True) -> list:
        """Fetch players from venue-tournaments platform using authenticated GraphQL via Playwright."""
        logger.info(f"Fetching venue players for {tournament_guid}...")
        payload = {
            "operationName": "GetPlayers",
            "variables": {
                "id": tournament_guid,
                "queryParameters": {
                    "limit": 0,
                    "offset": 0,
                    "sorts": [{"property": "playerLastName", "sortDirection": "ASCENDING"}],
                    "filters": []
                }
            },
            "query": VENUE_GET_PLAYERS_QUERY
        }
        with sync_playwright() as p:
            with p.chromium.launch_persistent_context(PLAYWRIGHT_USER_DIR, headless=headless) as context:
                page = context.pages[0] if context.pages else context.new_page()
                resp = page.request.post(
                    "https://prd-usta-kube-tournaments.clubspark.pro/",
                    data=json.dumps(payload),
                    headers={"Content-Type": "application/json"}
                )
                data = resp.json()
        items = data.get("data", {}).get("paginatedPublicTournamentRegistrations", {}).get("items", [])
        logger.info(f"Fetched {len(items)} players from venue API.")
        return items

    @staticmethod
    def parse_venue_players(items: list, target_event_ids: set = None) -> list:
        players = {}
        for item in items:
            custom_ids = item.get("playerCustomIds", [])
            usta_id = None
            for cid in custom_ids:
                if cid.get("key") == "ustaId":
                    usta_id = cid.get("value")
                    break
            if not usta_id:
                continue

            first = item.get("firstName") or ""
            last = item.get("lastName") or ""
            name = f"{last}, {first}" if first and last else item.get("playerName", "")

            player_event_ids = {e["id"] for e in item.get("events", []) if e.get("id")}

            if target_event_ids and not player_event_ids.intersection(target_event_ids):
                continue

            if usta_id not in players:
                players[usta_id] = {
                    "usta_id": usta_id,
                    "name": name,
                    "city": item.get("city"),
                    "state": item.get("state"),
                    "wtn_singles": None,
                    "wtn_doubles": None,
                    "ranking": None
                }
        return list(players.values())

    def get_tournament_info(self, url_or_id: str) -> dict:
        guid = self.extract_guid(url_or_id)
        payload = {
            "operationName": "GetTournament",
            "variables": {"id": guid.lower(), "previewMode": False},
            "query": TOURNAMENT_QUERY
        }
        resp = self.session.post(TOURNAMENT_ENDPOINT, json=payload, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json().get("data", {}).get("publishedTournament", {})
        timings = data.get("timings") or {}
        return {
            "name": data.get("name", "Unknown"),
            "start_date": timings.get("startDate", ""),
            "end_date": timings.get("endDate", ""),
            "guid": guid,
        }

    @staticmethod
    def parse_players(participants: list, target_event_ids: set = None) -> list:
        players = {}
        for p in participants:
            person = p.get("person") or {}
            usta_id = None
            for oid in person.get("personOtherIds", []):
                if oid.get("uniqueOrganisationName") == "USTA":
                    usta_id = oid.get("personId")
                    break
            if not usta_id:
                continue

            name = p.get("participantName") or f"{person.get('standardGivenName', '')} {person.get('standardFamilyName', '')}".strip()

            addresses = person.get("addresses", [])
            city = addresses[0].get("city") if addresses else None
            state = addresses[0].get("state") if addresses else None

            player_event_ids = {e["eventId"] for e in p.get("events", []) if e.get("eventId")}

            if target_event_ids and not player_event_ids.intersection(target_event_ids):
                continue

            if usta_id not in players:
                players[usta_id] = {
                    "usta_id": usta_id,
                    "name": name,
                    "city": city,
                    "state": state,
                    "wtn_singles": None,
                    "wtn_doubles": None,
                    "ranking": None
                }
        return list(players.values())

    @staticmethod
    def get_event_divisions(events: list) -> list:
        seen = set()
        divisions = []
        for e in events:
            div = e.get("division", {})
            display = USTAScraper._division_display_name(div)
            if display and display not in seen:
                seen.add(display)
                divisions.append(display)
        return divisions

    @staticmethod
    def _division_display_name(div: dict) -> str:
        age = div.get("ageCategory", {})
        evtype = str(div.get("eventType", "")).lower()
        gender = str(div.get("gender", "")).lower()
        max_age = age.get("maximumAge")
        min_age = age.get("minimumAge")
        age_part = f"U{max_age}" if max_age else (f"{min_age}+" if min_age else "")
        parts = [p for p in [evtype, gender, age_part] if p]
        return " ".join(parts).title() if parts else ""

    def get_tournament_divisions(self, url_or_id: str) -> list:
        events = self.fetch_events(self.extract_guid(url_or_id))
        return self.get_event_divisions(events)

    @staticmethod
    def build_event_filter(events: list, target_division: str = None) -> set:
        if not target_division:
            return None
        target_lower = target_division.lower()
        matched = set()
        for e in events:
            div = e.get("division", {})
            display_name = USTAScraper._division_display_name(div).lower()
            gender = str(div.get("gender", "")).lower()
            age = div.get("ageCategory", {})
            max_age = age.get("maximumAge")

            matched_pattern = False

            if target_lower in display_name:
                matched_pattern = True

            # 2) Check gender + age combination (e.g. "girls 12" matches "girls u12")
            target_has_gender = any(g in target_lower for g in ["boys", "girls"])
            target_age_num = None
            for word in target_lower.split():
                if word.isdigit():
                    target_age_num = int(word)
                    break

            if not matched_pattern and target_has_gender and target_age_num:
                gender_match = "girls" in target_lower and "girls" in gender
                gender_match = gender_match or ("boys" in target_lower and "boys" in gender)
                age_match = max_age is not None and target_age_num == max_age
                if gender_match and age_match:
                    matched_pattern = True

            if matched_pattern:
                matched.add(e["id"])
                logger.info(f"Matched event (id={e['id']}): {display_name}")

        if not matched:
            logger.warning(f"No event matched division filter '{target_division}'. Returning all participants.")
        return matched if matched else None

    def scrape_tournament(self, url_or_id: str, target_division: str = None,
                           headless: bool = True) -> list:
        guid = self.extract_guid(url_or_id)
        logger.info(f"Fetching tournament GUID: {guid}")

        events = self.fetch_events(guid)
        target_event_ids = self.build_event_filter(events, target_division)

        # Try standard public API first
        participants = self.fetch_participants(guid)
        if participants:
            players = self.parse_players(participants, target_event_ids)
        else:
            # Fall back to venue API (requires authenticated Playwright session)
            logger.info("Standard API returned no participants. Trying venue API (requires login)...")
            venue_items = self.fetch_venue_players(guid, headless=headless)
            if not venue_items:
                logger.warning("No participants from venue API either.")
                return []
            players = self.parse_venue_players(venue_items, target_event_ids)

        logger.info(f"Parsed {len(players)} players from tournament.")
        return players


    def fetch_player_profile(self, usta_id: str, headless: bool = True, browser=None) -> dict:
        logger.info(f"Fetching USTA profile for ID: {usta_id}")
        url = f"https://www.usta.com/en/home/play/player-search/profile.html#?uaid={usta_id}"

        own_browser = browser is None
        playwright = None
        if own_browser:
            playwright = sync_playwright().start()
            browser = playwright.chromium.launch(headless=headless)

        page = browser.new_page()
        api_data = {}

        def handle_response(resp):
            if "playerInfo" in resp.url:
                try:
                    api_data["info"] = resp.json()
                except Exception:
                    pass
            elif "playerRankings" in resp.url:
                try:
                    api_data["rankings"] = resp.json()
                except Exception:
                    pass

        page.on("response", handle_response)
        try:
            page.set_default_navigation_timeout(30000)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
        except Exception as e:
            logger.warning(f"Page load error for {usta_id}: {e}")
            page.close()
            if own_browser:
                browser.close()
                playwright.stop()
            return {}

        page.close()
        if own_browser:
            browser.close()
            playwright.stop()

        if not api_data.get("info"):
            logger.warning(f"No profile data returned for USTA ID {usta_id}")
            return {}

        return self._parse_and_save_profile(usta_id, api_data)

    def _parse_and_save_profile(self, usta_id: str, api_data: dict) -> dict:
        info_data = api_data.get("info") or {}
        raw_info = (info_data.get("data") or [{}])[0]
        raw_rankings_data = api_data.get("rankings") or {}
        raw_rankings = (raw_rankings_data.get("player") or {}).get("rankings") or []

        name = raw_info.get("name")
        if not name:
            logger.warning(f"No name found in profile data for USTA ID {usta_id}")
            return {}

        info = {
            "usta_id": usta_id,
            "name": name,
            "city": raw_info.get("city"),
            "state": raw_info.get("state"),
            "section": (raw_info.get("section") or {}).get("name"),
            "district": (raw_info.get("district") or {}).get("name"),
            "gender": raw_info.get("gender"),
            "age_category": raw_info.get("ageCategory"),
            "ball_color": (raw_info.get("ratings") or {}).get("ballColorRating"),
            "competition_level": (raw_info.get("ratings") or {}).get("competitionLevelBallColor"),
            "itf_tennis_id": raw_info.get("itfTennisId"),
            "nationality": raw_info.get("nationality"),
        }

        wtns = (raw_info.get("ratings") or {}).get("wtn") or []
        for w in wtns:
            t = w.get("type", "").upper()
            if t == "SINGLE":
                info["wtn_singles"] = w.get("tennisNumber")
                info["wtn_singles_confidence"] = w.get("confidence")
                info["wtn_singles_date"] = w.get("ratingDate")
            elif t == "DOUBLE":
                info["wtn_doubles"] = w.get("tennisNumber")
                info["wtn_doubles_confidence"] = w.get("confidence")
                info["wtn_doubles_date"] = w.get("ratingDate")

        rankings = []
        for r in raw_rankings:
            rnk = r.get("rank", {})
            rec = r.get("record", {})
            pts = r.get("pointsRecord", {})
            rankings.append({
                "display_label": r.get("displayLabel"),
                "age_restriction": r.get("ageRestriction"),
                "list_type": r.get("listType"),
                "match_format": r.get("matchFormat"),
                "rank_list_gender": r.get("rankListGender"),
                "rank_national": rnk.get("national"),
                "rank_section": rnk.get("section"),
                "rank_district": rnk.get("district"),
                "points": r.get("points"),
                "points_singles": pts.get("singlesPoints"),
                "points_doubles": pts.get("doublesPoints"),
                "points_bonus": pts.get("bonusPoints"),
                "wins": rec.get("win"),
                "losses": rec.get("loss"),
                "trend_direction": r.get("trendDirection"),
                "publish_date": r.get("publishDate"),
            })

        save_usta_player_profile(
            usta_id=info["usta_id"], name=info["name"],
            city=info["city"], state=info["state"],
            section=info["section"], district=info["district"],
            gender=info["gender"], age_category=info["age_category"],
            ball_color=info["ball_color"], competition_level=info["competition_level"],
            itf_tennis_id=info["itf_tennis_id"], nationality=info["nationality"],
            wtn_singles=info.get("wtn_singles"),
            wtn_singles_confidence=info.get("wtn_singles_confidence"),
            wtn_singles_date=info.get("wtn_singles_date"),
            wtn_doubles=info.get("wtn_doubles"),
            wtn_doubles_confidence=info.get("wtn_doubles_confidence"),
            wtn_doubles_date=info.get("wtn_doubles_date"),
            rankings=rankings,
        )

        result = {**info, "rankings": rankings}
        logger.info(f"Profile saved for {info.get('name')} ({usta_id}): "
                     f"{len(rankings)} ranking lists, WTN S={info.get('wtn_singles')} D={info.get('wtn_doubles')}")
        return result

    def fetch_profiles_for_tournament(self, url_or_id: str, target_division: str = None,
                                       headless: bool = True,
                                       progress_callback=None) -> list:
        players = self.scrape_tournament(url_or_id, target_division, headless=headless)
        results = []
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=headless)
        try:
            for i, p in enumerate(players):
                pct = (i + 1) / len(players)
                msg = f"[{i+1}/{len(players)}] {p['name']}"
                logger.info(msg)
                if progress_callback:
                    progress_callback(pct, msg)
                try:
                    profile = self.fetch_player_profile(p["usta_id"], headless=headless, browser=browser)
                    if profile:
                        results.append(profile)
                except Exception as e:
                    logger.warning(f"Failed to fetch profile for {p['usta_id']}: {e}")
        finally:
            browser.close()
            playwright.stop()
        return results


if __name__ == "__main__":
    import sys
    scraper = USTAScraper()
    if len(sys.argv) > 1 and sys.argv[1] == "profile":
        pid = sys.argv[2] if len(sys.argv) > 2 else "2019015217"
        res = scraper.fetch_player_profile(pid, headless=False)
        print(json.dumps(res, indent=2, default=str))
    else:
        res = scraper.scrape_tournament(
            "D6F0B896-6620-4052-B6C4-3ABBAE1C5A8B",
            target_division="Boys 14"
        )
        print(f"Scraped {len(res)} players.")
        for r in res[:5]:
            print(f"  {r['name']} | {r.get('city')}, {r.get('state')} | USTA ID: {r['usta_id']}")
