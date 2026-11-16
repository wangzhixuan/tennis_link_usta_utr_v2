import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scripts.service.tournament_finder as tf

print("=" * 60)
print("Tournament Finder: search merge + event tooltips")
print("=" * 60)

L7 = "00000000-0000-0000-0000-000000000007"
L6 = "00000000-0000-0000-0000-000000000006"
L5O = "00000000-0000-0000-0000-000000000005"


def make_resp(payload):
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = payload
    m.raise_for_status.return_value = None
    return m


def t_item(tid, name, level_name, level_id, event_id="e1"):
    return {
        "id": tid, "name": name,
        "level": {"id": level_id, "name": level_name},
        "startDateTime": "2026-12-01T00:00:00Z",
        "endDateTime": "2026-12-02T00:00:00Z",
        "primaryLocation": {"town": "San Diego", "county": "CA"},
        "registrationRestrictions": {"entriesCloseDateTime": "2026-11-20T00:00:00Z"},
        "events": [{"id": event_id, "division": {}, "surface": "hard",
                    "courtLocation": "outdoor"}],
        "url": "/Tournaments/Overview/x",
    }


# 1. Geocode (and cache)
# ──────────────────────────────────────────────────────────────────────
print("\n[1/5] Geocoding...")
tf._geocode_cache.clear()


def fake_get(url, params=None, headers=None, timeout=None):
    return make_resp([{"lat": "32.9473", "lon": "-117.2091"}])


with patch.object(tf.requests, "get", side_effect=fake_get) as g:
    assert tf.geocode_location("92130, CA") == (32.9473, -117.2091)
    assert tf.geocode_location("92130, ca") == (32.9473, -117.2091)  # cached
    assert g.call_count == 1
print("  PASSED")


# 2. Search: per-level radius, merge + de-dupe, sorted by distance
# ──────────────────────────────────────────────────────────────────────
print("\n[2/5] Search merge across levels...")
calls = []


def fake_post(url, json=None, headers=None, timeout=None):
    calls.append(json)
    lvl = None
    for f in json.get("filters", []):
        if f.get("key") == "tournament-level":
            lvl = f["items"][0]["value"]
    if lvl == L7:
        return make_resp({
            "total": 2,
            "searchResults": [
                {"distance": 1.0, "item": t_item("A", "Near L7", "Level 7", L7)},
                {"distance": 12.0, "item": t_item("B", "Far L7", "Level 7", L7)},
            ],
        })
    if lvl == L6:
        return make_resp({
            "total": 2,
            "searchResults": [
                {"distance": 1.0, "item": t_item("A", "Near L7", "Level 7", L7)},  # dup
                {"distance": 20.0, "item": t_item("C", "L6", "Level 6", L6)},
            ],
        })
    return make_resp({"total": 0, "searchResults": []})


with patch.object(tf, "geocode_location", return_value=(32.9, -117.2)), \
     patch.object(tf.requests, "post", side_effect=fake_post):
    results = tf.search_tournaments("92130, CA", {L7: 10, L6: 25})

assert len(results) == 3, [r["name"] for r in results]
# same start date -> sorted by level (L6 before L7), then distance
assert [r["id"] for r in results] == ["C", "A", "B"]
assert results[0]["distance"] == 20.0 and results[1]["distance"] == 1.0

# each level request used its own radius
radii = {}
for body in calls:
    lvl = next(f for f in body["filters"] if f["key"] == "tournament-level")["items"][0]["value"]
    radii[lvl] = next(f for f in body["filters"] if f["key"] == "distance")["items"][0]["value"]
assert radii[L7] == 10 and radii[L6] == 25, radii
print("  PASSED")


# 3. Event normalization (draw type / limit / surface / selection)
# ──────────────────────────────────────────────────────────────────────
print("\n[3/5] Event details normalization...")
payload = {
    "data": {"publishedTournament": {
        "id": "T1", "name": "Cup",
        "registrationRestrictions": {"entriesCloseDateTime": "2026-11-16T22:59:00Z"},
        "publishedEvents": [{
            "id": "E1", "courtLocation": "OUTDOOR", "surface": "HARD",
            "division": {"gender": "BOYS", "eventType": "SINGLES",
                         "ageCategory": {"todsCode": "U12", "minimumAge": None, "maximumAge": 12},
                         "ratingCategory": None},
            "formatConfiguration": {"drawSize": 48, "entriesLimit": 48, "eventFormat": "VC",
                                    "scoreFormat": "x", "selectionProcess": "BOTTOM_UP_BY_RANKING",
                                    "ballColour": "YELLOW"},
            "level": {"id": L5O, "name": "Level 5 Open", "category": "JUNIOR"},
            "pricing": {"entryFee": {"amount": 7500, "currency": "USD"}},
        }],
    }}
}
with patch.object(tf.requests, "post", return_value=make_resp(payload)):
    events = tf.fetch_tournament_events("T1")
ev = events["E1"]
assert ev["label"] == "Singles Boys U12", ev["label"]
assert ev["draw_type"] == "Voluntary consolation"
assert ev["draw_limit"] == 48
assert ev["surface"] == "Outdoor Hard"
assert ev["player_selection"] == "Bottom-up by ranking"
assert ev["registration_closed"] == "2026-11-16T22:59:00Z"
print("  PASSED")


# 4. Tooltip conditional rules
# ──────────────────────────────────────────────────────────────────────
print("\n[4/5] Tooltip rules...")
default_event = {
    "draw_type": "Single elimination", "draw_limit": 32,
    "surface": "Outdoor Hard", "registration_closed": "2026-11-16T22:59:00Z",
    "player_selection": "Top-down by ranking",
}
tip = tf.event_tooltip(default_event)
assert "Draw type: Single elimination" in tip
assert "Draw limit: 32" in tip
assert "Registrations closed:" in tip
assert "Surface:" not in tip                 # Outdoor Hard is omitted
assert "Player selection:" not in tip        # Top-down by ranking is omitted

odd_event = {
    "draw_type": "Voluntary consolation", "draw_limit": 16,
    "surface": "Indoor Clay", "registration_closed": "2026-11-16T22:59:00Z",
    "player_selection": "Bottom-up by ranking",
}
tip2 = tf.event_tooltip(odd_event)
assert "Surface: Indoor Clay" in tip2
assert "Player selection: Bottom-up by ranking" in tip2
print("  PASSED")


# 5. Geocode failure
# ──────────────────────────────────────────────────────────────────────
print("\n[5/5] Geocode failure handling...")
with patch.object(tf, "geocode_location", return_value=None):
    try:
        tf.search_tournaments("nowhere", {L7: 10})
        assert False, "should raise"
    except ValueError:
        pass
print("  PASSED")

print("\n" + "=" * 60)
print("Tournament Finder: ALL TESTS PASSED")
print("=" * 60)
