"""USTA tournament search + event details API client (Streamlit-free).

Public endpoints discovered from playtennis.usta.com:
  * POST prd-usta-kube.clubspark.pro/unified-search-api/api/Search/tournaments/Query
  * POST prd-usta-kube-tournaments.clubspark.pro/  (GetTournament GraphQL)

Location is geocoded to lat/lng (Nominatim), and each level is searched with its own
radius. Event tooltips are built from the GetTournament publishedEvents payload.
"""
import datetime
import logging
import re
import time

import requests

logger = logging.getLogger(__name__)

SEARCH_URL = ("https://prd-usta-kube.clubspark.pro/unified-search-api/api/Search/"
              "tournaments/Query?indexSchema=tournament")
TOURNAMENT_ENDPOINT = "https://prd-usta-kube-tournaments.clubspark.pro/"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/json",
    "Origin": "https://playtennis.usta.com",
    "Referer": "https://playtennis.usta.com/",
}
GEOCODE_HEADERS = {"User-Agent": "TennisLink/1.0 (local tool)"}

# Junior level options (from playtennis.usta.com tournaments-filters.json)
JUNIOR_LEVELS = [
    {"name": "Level 1", "id": "00000000-0000-0000-0000-000000000001"},
    {"name": "Level 2", "id": "00000000-0000-0000-0000-000000000002"},
    {"name": "Level 3 Open", "id": "00000000-0000-0000-0000-000000000003"},
    {"name": "Level 3 Closed", "id": "00000000-0000-0000-0000-0000000000b3"},
    {"name": "Level 4 Open", "id": "00000000-0000-0000-0000-000000000004"},
    {"name": "Level 4 Closed", "id": "00000000-0000-0000-0000-0000000000b4"},
    {"name": "Level 5 Open", "id": "00000000-0000-0000-0000-000000000005"},
    {"name": "Level 5 Closed", "id": "00000000-0000-0000-0000-0000000000b5"},
    {"name": "Level 6", "id": "00000000-0000-0000-0000-000000000006"},
    {"name": "Level 7", "id": "00000000-0000-0000-0000-000000000007"},
]

DRAW_TYPE_LABELS = {
    "se": "Single elimination",
    "rr": "Round robin",
    "vc": "Voluntary consolation",
    "cc": "Curtis consolation",
    "fic": "Feed-in consolation",
    "fmlc": "First match consolation",
    "mfic": "Modified feed-in consolation",
    "fic16": "Feed-in championship to round of 16",
    "ficq": "Feed-in championship to quarterfinals",
    "ficsf": "Feed-in championship to semifinals",
    "compass": "Compass draw",
}

SELECTION_LABELS = {
    "top down ranking": "Top-down by ranking",
    "top down by ranking": "Top-down by ranking",
    "top down rating": "Top-down by rating",
    "top down by rating": "Top-down by rating",
    "bottom up ranking": "Bottom-up by ranking",
    "bottom up by ranking": "Bottom-up by ranking",
    "bottom up rating": "Bottom-up by rating",
    "bottom up by rating": "Bottom-up by rating",
    "first come first served": "First come first served",
    "top down by wtn": "Top-down by WTN",
    "bottom up by wtn": "Bottom-up by WTN",
    "manual": "Manual",
}

TOURNAMENT_QUERY = """
query GetTournament($id: UUID!, $previewMode: Boolean) {
  publishedTournament(id: $id, previewMode: $previewMode) {
    id
    name
    identificationCode
    registrationRestrictions {
      entriesOpenDateTime
      entriesCloseDateTime
      timeZone
    }
    publishedEvents(previewMode: $previewMode) {
      id
      isPublished
      courtLocation
      surface
      division {
        gender
        eventType
        familyType
        ageCategory { todsCode minimumAge maximumAge }
        ratingCategory { ratingType value }
      }
      formatConfiguration {
        ballColour
        drawSize
        entriesLimit
        eventFormat
        scoreFormat
        selectionProcess
      }
      level { id name category orderIndex shortName }
      pricing { entryFee { amount currency } }
    }
  }
}
"""

_geocode_cache = {}
_last_geocode = [0.0]


def geocode_location(location):
    """Geocode a ZIP / 'City, ST' string to (lat, lon). Returns None on failure."""
    key = (location or "").strip().lower()
    if not key:
        return None
    if key in _geocode_cache:
        return _geocode_cache[key]

    # Nominatim asks for at most ~1 request/second.
    elapsed = time.time() - _last_geocode[0]
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": location, "format": "json", "limit": 1},
            headers=GEOCODE_HEADERS, timeout=20,
        )
        _last_geocode[0] = time.time()
        if resp.status_code != 200:
            logger.warning(f"Geocode returned {resp.status_code} for {location!r}")
            return None
        data = resp.json()
        if not data:
            logger.warning(f"No geocode result for {location!r}")
            return None
        coords = (float(data[0]["lat"]), float(data[0]["lon"]))
        _geocode_cache[key] = coords
        return coords
    except Exception as e:
        logger.warning(f"Geocode failed for {location!r}: {e}")
        return None


def _date_window(date_from=None, date_to=None):
    now = datetime.datetime.utcnow()
    if date_from is None:
        date_from = now.strftime("%Y-%m-%dT00:00:00.000Z")
    if date_to is None:
        date_to = (now + datetime.timedelta(days=45)).strftime("%Y-%m-%dT23:59:59.999Z")
    return date_from, date_to


def level_sort_key(level_name):
    """Order levels by number, then Open before Closed: L1, L2, L3 Open, L3 Closed, ..."""
    m = re.search(r"level\s*(\d+)", level_name or "", re.I)
    number = int(m.group(1)) if m else 99
    variant = 1 if "closed" in (level_name or "").lower() else 0
    return (number, variant)


def tournament_sort_key(t):
    dist = t.get("distance")
    return (t.get("start") or "9999", level_sort_key(t.get("level")),
            dist if dist is not None else 9999)


def _normalize_tournament(item, distance, level):
    loc = item.get("primaryLocation") or {}
    reg = item.get("registrationRestrictions") or {}
    url = item.get("url") or ""
    if url and url.startswith("/"):
        url = "https://playtennis.usta.com" + url
    events = []
    for ev in item.get("events") or []:
        if not ev.get("id"):
            continue
        events.append({
            "id": ev.get("id"),
            "division": ev.get("division") or {},
            "surface": ev.get("surface"),
            "court_location": ev.get("courtLocation"),
        })
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "level": (item.get("level") or {}).get("name") or level["name"],
        "level_id": (item.get("level") or {}).get("id") or level["id"],
        "start": item.get("startDateTime"),
        "end": item.get("endDateTime"),
        "entry_deadline": reg.get("entriesCloseDateTime"),
        "city": loc.get("town"),
        "state": loc.get("county"),
        "distance": distance,
        "url": url,
        "events": events,
    }


def search_tournaments(location, level_radius, date_from=None, date_to=None,
                       page_size=100, max_pages=3):
    """
    Search USTA tournaments, one request per included level (with that level's radius),
    and merge/de-duplicate the results by tournament id.

    level_radius: {level_uuid: max_miles} — only levels present here are searched.
    Returns a list sorted by start date, then level, then distance.
    """
    coords = geocode_location(location)
    if not coords:
        raise ValueError(f"Could not geocode location: {location!r}")
    lat, lon = coords
    date_from, date_to = _date_window(date_from, date_to)

    merged = {}
    for level in JUNIOR_LEVELS:
        radius = level_radius.get(level["id"])
        if not radius:
            continue
        for page in range(max_pages):
            body = {
                "options": {
                    "size": page_size, "from": page * page_size, "sortKey": "date",
                    "latitude": lat, "longitude": lon,
                },
                "filters": [
                    {"key": "publish-target", "items": [{"value": 1}]},
                    {"key": "level-category", "items": [{"value": "junior"}], "operator": "Or"},
                    {"key": "date-range", "operator": "Or", "items": [
                        {"minDate": date_from, "maxDate": date_to}]},
                    {"key": "distance", "operator": "Or", "items": [{"value": int(radius)}]},
                    {"key": "tournament-level", "operator": "Or",
                     "items": [{"value": level["id"]}]},
                ],
            }
            try:
                resp = requests.post(SEARCH_URL, json=body, headers=SEARCH_HEADERS, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.warning(f"Search failed for {level['name']}: {e}")
                break

            results = data.get("searchResults") or []
            for res in results:
                item = res.get("item") or {}
                tid = item.get("id")
                if not tid:
                    continue
                dist = res.get("distance")
                norm = _normalize_tournament(item, dist, level)
                prev = merged.get(tid)
                if prev is None or (dist is not None and
                                    (prev.get("distance") is None or dist < prev["distance"])):
                    merged[tid] = norm

            total = data.get("total") or 0
            if not results or (page + 1) * page_size >= total:
                break

    return sorted(merged.values(), key=tournament_sort_key)


def _division_label(division):
    et = (division.get("eventType") or "").replace("_", " ").title()
    gender = (division.get("gender") or "").replace("_", " ").title()
    age = (division.get("ageCategory") or {}).get("todsCode") or ""
    return " ".join(p for p in (et, gender, age) if p)


def _surface_label(court_location, surface):
    court = (court_location or "").replace("_", " ").title()
    surf = (surface or "").replace("_", " ").replace("-", " ").title()
    return " ".join(p for p in (court, surf) if p)


def _draw_type_label(code):
    if not code:
        return None
    return DRAW_TYPE_LABELS.get(code.lower(), code.upper())


def _selection_label(code):
    if not code:
        return None
    key = code.lower().replace("_", " ")
    return SELECTION_LABELS.get(key, key.title())


def _format_date(value):
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y").replace(" 0", " ")
    except Exception:
        return str(value)


def normalize_event(event, registration):
    division = event.get("division") or {}
    fmt = event.get("formatConfiguration") or {}
    return {
        "id": event.get("id"),
        "label": _division_label(division),
        "division": division,
        "draw_type": _draw_type_label(fmt.get("eventFormat")),
        "draw_limit": fmt.get("entriesLimit") or fmt.get("drawSize"),
        "surface": _surface_label(event.get("courtLocation"), event.get("surface")),
        "registration_closed": (registration or {}).get("entriesCloseDateTime"),
        "player_selection": _selection_label(fmt.get("selectionProcess")),
        "level": (event.get("level") or {}).get("name"),
    }


def fetch_tournament_events(tournament_id):
    """Fetch all published events (with details) for a tournament. Returns {event_id: event}."""
    body = {
        "operationName": "GetTournament",
        "variables": {"id": str(tournament_id), "previewMode": False},
        "query": TOURNAMENT_QUERY,
    }
    resp = requests.post(TOURNAMENT_ENDPOINT, json=body, headers=SEARCH_HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        logger.warning(f"GetTournament errors for {tournament_id}: {data['errors'][:1]}")
    pt = ((data.get("data") or {}).get("publishedTournament")) or {}
    reg = pt.get("registrationRestrictions") or {}
    events = {}
    for ev in pt.get("publishedEvents") or []:
        if ev.get("id"):
            events[ev["id"]] = normalize_event(ev, reg)
    return events


def event_tooltip(event):
    """
    Conditional tooltip: always Draw type / Draw limit / Registrations closed;
    Surface only when not 'Outdoor Hard'; Player selection only when not
    'Top-down by ranking'.
    """
    parts = []
    if event.get("draw_type"):
        parts.append(f"Draw type: {event['draw_type']}")
    if event.get("draw_limit"):
        parts.append(f"Draw limit: {event['draw_limit']}")
    surface = event.get("surface")
    if surface and surface != "Outdoor Hard":
        parts.append(f"Surface: {surface}")
    closed = _format_date(event.get("registration_closed"))
    if closed:
        parts.append(f"Registrations closed: {closed}")
    selection = event.get("player_selection")
    if selection and selection != "Top-down by ranking":
        parts.append(f"Player selection: {selection}")
    return " • ".join(parts)
