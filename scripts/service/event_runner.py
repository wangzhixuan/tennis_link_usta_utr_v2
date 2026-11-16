"""Run the full enrichment pipeline for a single tournament event (Streamlit-free).

Used by the Tournament Finder. Returns a DataFrame and writes a TSV report, mirroring
the main app's single-tournament run but scoped to one event id.
"""
import os
import re
import json
import datetime
import logging

import pandas as pd

from scripts.service.db import (get_connection, save_usta_player_history, save_utr_player_history,
                                get_checked_event, get_checked_event_for_label, save_checked_event)
from scripts.service.usta_scraper import USTAScraper
from scripts.service.utr_scraper import UTRScraper
from scripts.service.matcher import PlayerMatcher
from scripts.service.render import short_ranking_label

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data", "tournaments")


def _val(v, default="N/A"):
    return v if v is not None else default


def _updated_today(iso_str):
    if not iso_str:
        return False
    try:
        return datetime.datetime.fromisoformat(iso_str).date() == datetime.date.today()
    except Exception:
        return False


def _division_string(division):
    gender = (division.get("gender") or "").replace("_", " ").title()
    age = (division.get("ageCategory") or {}).get("todsCode") or ""
    return " ".join(p for p in (gender, age) if p)


def _division_from_label(label):
    """Best-effort division dict from a label like 'Singles Girls U14'."""
    parts = str(label or "").replace("'", " ").split()
    gender = next((p.upper() for p in parts if p.lower() in ("girls", "boys")), None)
    age = next((p for p in parts if p.isdigit() or
                (p.lower().startswith("u") and p[1:].isdigit())), None)
    if age and age.lower().startswith("u"):
        age = age[1:]
    out = {}
    if gender:
        out["gender"] = gender
    if age:
        out["ageCategory"] = {"todsCode": f"U{age}"}
    return out


def build_ranking_list_name(division, gender=None):
    if not division:
        return None
    parts = division.split()
    gender_word = next((p.capitalize() for p in parts if p.lower() in ("girls", "boys")), None)
    age = next((p for p in parts if p.isdigit() or
                (p.lower().startswith("u") and p[1:].isdigit())), None)
    if age and age.lower().startswith("u"):
        age = age[1:]
    if gender_word is None and gender:
        g = (gender or "").upper()
        if g in ("F", "FEMALE"):
            gender_word = "Girls"
        elif g in ("M", "MALE"):
            gender_word = "Boys"
    if gender_word and age:
        return f"{gender_word}' {age} National Standings List (combined)"
    return None


def get_points_for_list(rankings, list_name):
    if not list_name:
        return None
    lower = list_name.lower()
    for r in rankings or []:
        if (r.get("display_label") or "").lower() == lower:
            return r.get("points")
    return None


def _output_path(tournament_info, division, event_id=None):
    name = tournament_info.get("name") or "Unknown"
    guid = (tournament_info.get("guid") or "")[:8]
    date_raw = tournament_info.get("start_date", "")
    yyyymmdd = date_raw[:10].replace("-", "") if date_raw else "unknown"
    safe_name = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_")
    safe_div = re.sub(r"[^a-zA-Z0-9]+", "_", division).strip("_") if division else "All"
    parts = f"Tournament.{yyyymmdd}.{safe_name}.{guid}.{safe_div}"
    if event_id:
        parts += f".{str(event_id)[:8]}"
    fname = parts + ".tsv"
    os.makedirs(DATA_DIR, exist_ok=True)
    return fname, os.path.join(DATA_DIR, fname)


def _is_mixed_team_division(division):
    lower = (division or "").lower()
    return "mixed" in lower and "team" in lower


def run_event(tournament_id, event, run_profiles=True, no_login=False,
              no_cross_ref=False, progress=None, usta=None, utr=None):
    """
    Run the full pipeline for one event.

    event: normalized event dict from scripts.service.tournament_finder (needs id + division).
    Returns {ok, df, ranking_label, fname, out_path, rate_limited, error}.
    """
    log = progress or (lambda m: None)
    usta = usta or USTAScraper()
    division = event.get("division") or {}
    division_str = _division_string(division) or (event.get("label") or "")
    if not division:
        division = _division_from_label(division_str)
    ranking_list_name = build_ranking_list_name(division_str)
    ranking_label = short_ranking_label(division_str) or "Rank Pts"
    player_gender_field = (division.get("gender") or "").upper()
    is_mixed = player_gender_field in ("MIXED", "COED")

    try:
        t_info = usta.get_tournament_info(tournament_id)
    except Exception as e:
        return {"ok": False, "error": f"Tournament info failed: {e}"}

    log(f"Fetching players for {t_info.get('name')} — {event.get('label')}...")
    try:
        players = usta.scrape_tournament(
            tournament_id, division_str, headless=True,
            target_event_ids={event.get("id")},
        )
    except Exception as e:
        return {"ok": False, "error": f"Player fetch failed: {e}"}
    if not players:
        return {"ok": False, "error": "No players found for this event."}
    log(f"Found {len(players)} players.")

    # Compare against the previously saved roster (new registrations)
    event_key = f"{tournament_id}:{event.get('id')}"
    previous = get_checked_event(event_key)
    previous_ids = ({str(p.get("usta_id")) for p in (previous or {}).get("roster", [])}
                    if previous else set())
    current_ids = {str(p["usta_id"]) for p in players}
    new_ids = (current_ids - previous_ids) if previous else set()
    if new_ids:
        log(f"New players since last check: {len(new_ids)}")

    utr = utr or UTRScraper()
    if not no_login:
        utr.login_if_needed()
    matcher = PlayerMatcher(utr)
    rate_limited = UTRScraper.is_rate_limited()

    conn = get_connection()
    records = []
    try:
        for idx, up in enumerate(players):
            usta_id = up["usta_id"]
            log(f"[{idx+1}/{len(players)}] {up.get('name')}")

            # ── USTA profile (refresh unless already pulled today) ──
            row = conn.execute(
                "SELECT * FROM usta_player_profiles WHERE usta_id = ?", (usta_id,)
            ).fetchone()
            cached = dict(row) if row else None
            if cached and cached.get("wtn_singles") is None and cached.get("wtn_singles_confidence") is not None:
                cached = None

            if run_profiles and not (cached and _updated_today(cached.get("last_updated"))):
                try:
                    profile = usta.fetch_player_profile(usta_id, headless=True)
                    if profile:
                        up.update(profile)
                        save_usta_player_history(usta_id, profile)
                except Exception as e:
                    logger.warning(f"Profile fetch failed for {usta_id}: {e}")
                usta_ts = datetime.datetime.now().isoformat()
            elif cached:
                up["wtn_singles"] = cached.get("wtn_singles")
                up["wtn_doubles"] = cached.get("wtn_doubles")
                up["section"] = cached.get("section")
                up["district"] = cached.get("district")
                up["gender"] = cached.get("gender")
                rj = cached.get("ranking_json")
                if rj:
                    up["rankings"] = json.loads(rj)
                else:
                    up["rankings"] = [dict(r) for r in conn.execute(
                        "SELECT * FROM usta_player_rankings WHERE usta_id = ?", (usta_id,)
                    ).fetchall()]
                usta_ts = cached.get("last_updated", "")
            else:
                usta_ts = ""

            pg = (up.get("gender") or "").upper()
            points_list = ranking_list_name
            if is_mixed:
                points_list = build_ranking_list_name(division_str, pg)
            points = get_points_for_list(up.get("rankings", []), points_list)

            # ── UTR ──
            _rl = UTRScraper.is_rate_limited()
            if _rl:
                rate_limited = True
            match_res = matcher.find_utr_profile(
                up, players, no_cross_ref=no_cross_ref, cache_only=_rl
            )

            utr_id = None
            utr_singles = None
            utr_doubles = None
            utr_ts = ""
            if match_res:
                utr_id = str(match_res["utr_id"])
                urow = conn.execute(
                    "SELECT * FROM utr_player_profiles WHERE utr_id = ?", (utr_id,)
                ).fetchone()
                if urow:
                    uc = dict(urow)
                    utr_ts = uc.get("last_updated", "")
                    cs = uc.get("utr_singles")
                    if cs is not None and cs != 0.0 and cs != int(cs):
                        utr_singles = cs
                        utr_doubles = uc.get("utr_doubles")

                if utr_singles is None or utr_singles == 0.0:
                    if UTRScraper.is_rate_limited():
                        rate_limited = True
                    else:
                        profile = utr.get_player_profile(utr_id)
                        if profile is None and utr.last_profile_not_found:
                            new_match = matcher.rematch_player(
                                up, players, no_cross_ref=no_cross_ref,
                                exclude_utr_ids={utr_id},
                            )
                            if new_match:
                                utr_id = str(new_match["utr_id"])
                                profile = utr.get_player_profile(utr_id)
                        if profile:
                            utr_singles = profile.get("utr_singles")
                            utr_doubles = profile.get("utr_doubles")
                            utr_ts = datetime.datetime.now().isoformat()
                            save_utr_player_history(utr_id, profile)

            gender_display = ("F" if pg in ("F", "FEMALE")
                              else "M" if pg in ("M", "MALE") else "")
            record = {
                "Player Name": up["name"],
                "New": "NEW" if str(usta_id) in new_ids else "",
                "Hometown": f"{up.get('city') or ''}, {up.get('state') or ''}".strip(", "),
                "USTA ID": usta_id,
                "Status": _val(up.get("status")),
                "Gender": gender_display,
                "WTN": _val(up.get("wtn_singles")),
                ranking_label: _val(points),
                "Profile Updated": usta_ts,
                "UTR ID": _val(utr_id),
                "UTR": _val(utr_singles),
                "UTR Updated": utr_ts,
            }
            records.append(record)
    finally:
        conn.close()

    if not records:
        return {"ok": False, "error": "No records produced."}

    df = pd.DataFrame(records)
    col_order = ["Player Name", "New", "Hometown", "USTA ID", "Status"]
    if is_mixed:
        col_order.append("Gender")
    col_order += ["WTN", ranking_label, "Profile Updated",
                  "UTR ID", "UTR", "UTR Updated"]
    df = df[col_order]

    fname, out_path = _output_path(t_info, division_str, event.get("id"))
    df.to_csv(out_path, index=False, sep="\t")

    try:
        roster = [{"usta_id": str(p["usta_id"]), "name": p.get("name"),
                   "status": p.get("status")} for p in players]
        save_checked_event(
            event_key, tournament_id, event.get("id"), t_info.get("name"),
            event.get("label"), event.get("level"), division, roster,
            event_date=t_info.get("start_date"),
        )
    except Exception as e:
        logger.warning(f"Failed to save checked event {event_key}: {e}")

    return {"ok": True, "df": df, "ranking_label": ranking_label,
            "fname": fname, "out_path": out_path, "rate_limited": rate_limited,
            "new_count": len(new_ids),
            "previous_count": len(previous_ids) if previous else None}


def run_division(tournament, division="", run_profiles=True, no_login=False,
                 no_cross_ref=False, force_refresh_ids=None, headless=True,
                 progress=None, usta=None, utr=None):
    """Run the full pipeline for a tournament/division (the Tournament Players flow)."""
    log = progress or (lambda m: None)
    usta = usta or USTAScraper()
    division_label = division or "All divisions"
    ranking_list_name = build_ranking_list_name(division)
    ranking_list_label = short_ranking_label(division) or "Rank Pts"
    is_mixed = _is_mixed_team_division(division)
    force_ids = {str(x) for x in (force_refresh_ids or set())}

    try:
        t_info = usta.get_tournament_info(tournament)
    except Exception as e:
        return {"ok": False, "error": f"Tournament info failed: {e}"}

    log(f"Fetching players for {t_info.get('name')} — {division_label}...")
    try:
        players = usta.scrape_tournament(tournament, division, headless=headless)
    except Exception as e:
        return {"ok": False, "error": f"Player fetch failed: {e}"}
    if not players:
        return {"ok": False, "error": "No players found."}
    log(f"Found {len(players)} players.")

    tournament_guid = t_info.get("guid") or str(tournament)
    event_key = f"{tournament_guid}:division:{division_label}"
    previous = get_checked_event(event_key) or get_checked_event_for_label(
        tournament_guid, division_label
    )
    previous_ids = ({str(p.get("usta_id")) for p in (previous or {}).get("roster", [])}
                    if previous else set())
    current_ids = {str(p["usta_id"]) for p in players}
    new_ids = (current_ids - previous_ids) if previous else set()
    if new_ids:
        log(f"{len(new_ids)} new player(s) since the last check.")

    utr = utr or UTRScraper()
    if not no_login:
        utr.login_if_needed()
    rate_limited = UTRScraper.is_rate_limited()
    matcher = PlayerMatcher(utr)

    conn = get_connection()
    records = []
    try:
        for idx, up in enumerate(players):
            usta_id = up["usta_id"]
            log(f"[{idx+1}/{len(players)}] {up.get('name')}")

            row = conn.execute(
                "SELECT * FROM usta_player_profiles WHERE usta_id = ?", (usta_id,)
            ).fetchone()
            cached = dict(row) if row else None
            if cached and cached.get("wtn_singles") is None and cached.get("wtn_singles_confidence") is not None:
                cached = None

            if run_profiles and not (cached and _updated_today(cached.get("last_updated"))):
                try:
                    profile = usta.fetch_player_profile(usta_id, headless=headless)
                    if profile:
                        up.update(profile)
                        save_usta_player_history(usta_id, profile)
                except Exception as e:
                    logger.warning(f"Profile fetch failed for {usta_id}: {e}")
                usta_ts = datetime.datetime.now().isoformat()
            elif cached:
                up["wtn_singles"] = cached.get("wtn_singles")
                up["wtn_doubles"] = cached.get("wtn_doubles")
                up["section"] = cached.get("section")
                up["district"] = cached.get("district")
                up["gender"] = cached.get("gender")
                rj = cached.get("ranking_json")
                if rj:
                    up["rankings"] = json.loads(rj)
                else:
                    up["rankings"] = [dict(r) for r in conn.execute(
                        "SELECT * FROM usta_player_rankings WHERE usta_id = ?", (usta_id,)
                    ).fetchall()]
                usta_ts = cached.get("last_updated", "")
            else:
                usta_ts = ""

            pg = (up.get("gender") or "").upper()
            points_list = ranking_list_name
            if is_mixed:
                points_list = build_ranking_list_name(division, pg)
            points = get_points_for_list(up.get("rankings", []), points_list)

            _rl = UTRScraper.is_rate_limited()
            if _rl and not rate_limited:
                rate_limited = True
                log("UTR rate limit hit — using cached UTR data only for the rest of this run.")
            match_res = matcher.find_utr_profile(
                up, players, no_cross_ref=no_cross_ref, cache_only=_rl
            )

            utr_id = None
            utr_singles = None
            utr_doubles = None
            utr_ts = ""
            if match_res:
                utr_id = str(match_res["utr_id"])
                urow = conn.execute(
                    "SELECT * FROM utr_player_profiles WHERE utr_id = ?", (utr_id,)
                ).fetchone()
                force = usta_id in force_ids
                if urow:
                    uc = dict(urow)
                    utr_ts = uc.get("last_updated", "")
                    cs = uc.get("utr_singles")
                    use_cache = not force or _updated_today(utr_ts)
                    if use_cache and cs is not None and cs != 0.0 and cs != int(cs):
                        utr_singles = cs
                        utr_doubles = uc.get("utr_doubles")

                if utr_singles is None or utr_singles == 0.0:
                    if UTRScraper.is_rate_limited():
                        rate_limited = True
                    else:
                        profile = utr.get_player_profile(utr_id)
                        if profile is None and utr.last_profile_not_found:
                            new_match = matcher.rematch_player(
                                up, players, no_cross_ref=no_cross_ref,
                                exclude_utr_ids={utr_id},
                            )
                            if new_match:
                                utr_id = str(new_match["utr_id"])
                                profile = utr.get_player_profile(utr_id)
                        if profile:
                            utr_singles = profile.get("utr_singles")
                            utr_doubles = profile.get("utr_doubles")
                            utr_ts = datetime.datetime.now().isoformat()
                            save_utr_player_history(utr_id, profile)

            gender_display = ("F" if pg in ("F", "FEMALE")
                              else "M" if pg in ("M", "MALE") else "")
            records.append({
                "Player Name": up["name"],
                "New": "NEW" if str(usta_id) in new_ids else "",
                "Hometown": f"{up.get('city') or ''}, {up.get('state') or ''}".strip(", "),
                "USTA ID": usta_id,
                "Status": _val(up.get("status")),
                "Gender": gender_display,
                "WTN": _val(up.get("wtn_singles")),
                ranking_list_label: _val(points),
                "Profile Updated": usta_ts,
                "UTR ID": _val(utr_id),
                "UTR": _val(utr_singles),
                "UTR Updated": utr_ts,
            })
    finally:
        conn.close()

    # Second pass for any still-missing/0.0 UTR (fresh API, if logged in)
    if not no_login and not UTRScraper.is_rate_limited():
        usta_lookup = {p["usta_id"]: p for p in players}
        for rec in records:
            if UTRScraper.is_rate_limited():
                break
            uid = rec["UTR ID"]
            if uid in ("N/A", None, ""):
                continue
            try:
                val = float(rec["UTR"])
            except (ValueError, TypeError):
                val = None
            if val is None or val == 0.0:
                profile = utr.get_player_profile(uid)
                if profile is None and utr.last_profile_not_found:
                    up_ref = usta_lookup.get(str(rec["USTA ID"]))
                    if up_ref:
                        new_match = matcher.rematch_player(
                            up_ref, players, no_cross_ref=no_cross_ref,
                            exclude_utr_ids={str(uid)},
                        )
                        if new_match:
                            rec["UTR ID"] = str(new_match["utr_id"])
                            profile = utr.get_player_profile(rec["UTR ID"])
                if profile and profile.get("utr_singles") is not None and profile["utr_singles"] != 0.0:
                    rec["UTR"] = profile["utr_singles"]
                    rec["UTR Updated"] = datetime.datetime.now().isoformat()

    if not records:
        return {"ok": False, "error": "No records produced."}

    df = pd.DataFrame(records)
    col_order = ["Player Name", "New", "Hometown", "USTA ID", "Status"]
    if is_mixed:
        col_order.append("Gender")
    col_order += ["WTN", ranking_list_label, "Profile Updated",
                  "UTR ID", "UTR", "UTR Updated"]
    df = df[col_order]

    fname, out_path = _output_path(t_info, division, event_id=None)
    df.to_csv(out_path, index=False, sep="\t")

    try:
        roster = [{"usta_id": str(p["usta_id"]), "name": p.get("name"),
                   "status": p.get("status")} for p in players]
        save_checked_event(
            event_key, tournament_guid, None, t_info.get("name"), division_label,
            None, _division_from_label(division), roster, event_date=t_info.get("start_date"),
        )
    except Exception as e:
        logger.warning(f"Failed to save checked event {event_key}: {e}")

    return {"ok": True, "df": df, "ranking_label": ranking_list_label,
            "fname": fname, "out_path": out_path, "rate_limited": rate_limited,
            "new_count": len(new_ids),
            "previous_count": len(previous_ids) if previous else None}
