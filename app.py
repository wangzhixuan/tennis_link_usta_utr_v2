import os
import re
import io
import json
import sqlite3
import datetime
import pandas as pd
import streamlit as st

from scripts.config import (UTR_USER, UTR_PASS, DB_PATH,
                            BOOTSTRAP_MIN_PLAYERS, BOOTSTRAP_MAX_PLAYERS, BOOTSTRAP_MAX_DEPTH)
from scripts.usta_scraper import USTAScraper
from scripts.utr_scraper import UTRScraper
from scripts.matcher import PlayerMatcher
from scripts.db import (get_connection, save_mapping, save_usta_player_history,
                        save_utr_player_history, count_mappings)

st.set_page_config(page_title="TennisLink", layout="wide", initial_sidebar_state="expanded")

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tournaments")
USTA_PROFILE_URL = "https://www.usta.com/en/home/play/player-search/profile.html#?uaid={}"
UTR_PROFILE_URL = "https://app.utrsports.net/profiles/{}"


def check_usta_login() -> bool:
    """Check if the persistent Playwright context has a valid www.usta.com session."""
    from playwright.sync_api import sync_playwright
    from scripts.utr_scraper import PLAYWRIGHT_USER_DIR
    try:
        with sync_playwright() as p:
            with p.chromium.launch_persistent_context(PLAYWRIGHT_USER_DIR, headless=True) as context:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://www.usta.com/en/home/play/player-search/profile.html#?uaid=2019015217&tab=results",
                           wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(3000)
                sign_in = page.query_selector("text=SIGN IN")
                return sign_in is None or not sign_in.is_visible()
    except Exception:
        return False


def check_utr_login() -> bool:
    """Check if the persistent Playwright context has a valid UTR JWT."""
    from playwright.sync_api import sync_playwright
    from scripts.utr_scraper import PLAYWRIGHT_USER_DIR
    try:
        with sync_playwright() as p:
            with p.chromium.launch_persistent_context(PLAYWRIGHT_USER_DIR, headless=True) as context:
                cookies = context.cookies("https://api.utrsports.net")
                return any(c["name"] == "jwt" for c in cookies)
    except Exception:
        return False


def open_login_browser(service: str):
    """Open a headed browser for manual login to USTA or UTR."""
    from playwright.sync_api import sync_playwright
    from scripts.utr_scraper import PLAYWRIGHT_USER_DIR
    url = "https://www.usta.com/en/home/play/player-search/profile.html#?uaid=2019015217" if service == "USTA" \
        else "https://app.utrsports.net/login"
    with sync_playwright() as p:
        with p.chromium.launch_persistent_context(PLAYWRIGHT_USER_DIR, headless=False) as context:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            st.info(f"{service} browser opened. Complete login and close the window.")
            try:
                page.wait_for_event("close", timeout=120000)
            except:
                pass
            st.success(f"{service} login attempt completed. Check status again.")


def is_mixed_team_division(division: str) -> bool:
    if not division:
        return False
    lower = division.lower()
    return "mixed" in lower and "team" in lower


def build_ranking_list_name(division: str, gender: str = None) -> str:
    if not division:
        return None
    parts = division.split()
    gender_word = next((p.capitalize() for p in parts if p.lower() in ("girls", "boys")), None)
    age = next((p for p in parts if p.isdigit() or (p.lower().startswith("u") and p[1:].isdigit())), None)
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


def get_points_for_list(rankings: list, list_name: str):
    if not list_name:
        return None
    lower = list_name.lower()
    for r in rankings:
        if r.get("display_label", "").lower() == lower:
            return r.get("points")
    return None


def days_old(iso_str: str) -> float:
    if not iso_str:
        return 60.0
    try:
        dt = datetime.datetime.fromisoformat(iso_str)
        return max(0.0, min(60.0, (datetime.datetime.now() - dt).total_seconds() / 86400))
    except Exception:
        return 60.0


def staleness_color(days: float) -> str:
    ratio = days / 60.0
    r = int(255 * ratio)
    g = int(180 * (1 - ratio))
    b = 0
    return f"rgb({r},{g},{b})"


def render_cell(val) -> str:
    return f"<span style='font-size:14px'>{val}</span>"


def render_hyperlink(url: str, text: str) -> str:
    return f"<a href='{url}' target='_blank' style='font-size:14px'>{text}</a>"


def render_timestamp(iso_str: str) -> str:
    d = days_old(iso_str)
    color = staleness_color(d)
    display = "N/A"
    tooltip = ""
    if iso_str:
        try:
            dt = datetime.datetime.fromisoformat(iso_str)
            display = dt.strftime("%m-%d")
            tooltip = dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            display = iso_str
            tooltip = iso_str
    if tooltip:
        return f"<span style='font-size:14px;color:{color};cursor:help' title='{tooltip}'>{display}</span>"
    return f"<span style='font-size:14px;color:{color}'>{display}</span>"


def _val(v, default="N/A"):
    return v if v is not None else default


def _updated_today(iso_str) -> bool:
    if not iso_str:
        return False
    try:
        return datetime.datetime.fromisoformat(iso_str).date() == datetime.date.today()
    except Exception:
        return False


# ── Mapping bootstrap ──────────────────────────────────────────────
def render_bootstrap_tab():
    """Bootstrap the mapping DB from a known pair (its own tab)."""
    st.subheader("Bootstrap mapping DB")
    try:
        count = count_mappings()
    except Exception:
        count = 0
    st.caption(f"Current mapping DB: **{count}** player(s).")

    result = st.session_state.get("_bootstrap_result")
    if result and result.get("ok"):
        render_bootstrap_summary()
        st.divider()

    if count >= BOOTSTRAP_MIN_PLAYERS:
        st.info(
            f"Mapping DB already has {count} players (≥ {BOOTSTRAP_MIN_PLAYERS}). "
            f"You can still seed an extra pair below."
        )

    st.markdown(
        "Seed the DB from one **USTA ↔ UTR pair you already know**. TennisLink then "
        "cross-checks each player's match history (date + score + opponent) to discover "
        "more high-confidence pairs."
    )
    with st.expander("Seed from a known pair", expanded=True):
        usta_ok = st.session_state._usta_login
        utr_ok = st.session_state._utr_login
        c1, c2, c3 = st.columns([1, 1, 2])
        c1.markdown(f"**USTA:** {'✅' if usta_ok else '❌'}")
        c2.markdown(f"**UTR:** {'✅' if utr_ok else '❌'}")
        if st.button("🔍 Check logins", key="bs_check"):
            with st.spinner("Checking USTA/UTR sessions..."):
                st.session_state._usta_login = check_usta_login()
                st.session_state._utr_login = check_utr_login()
            st.rerun()

        if not (usta_ok and utr_ok):
            st.info("Bootstrap requires **both** USTA and UTR logins. Log in first.")
            lg1, lg2 = st.columns(2)
            if not usta_ok and lg1.button("🔑 Login USTA", key="bs_lg_usta", use_container_width=True):
                open_login_browser("USTA")
                st.session_state._usta_login = None
                st.rerun()
            if not utr_ok and lg2.button("🔑 Login UTR", key="bs_lg_utr", use_container_width=True):
                open_login_browser("UTR")
                st.session_state._utr_login = None
                st.rerun()
            return

        st.caption("Enter a pair you already know (both numeric IDs).")
        col_a, col_b = st.columns(2)
        seed_usta = col_a.text_input("USTA ID", key="bs_usta_id", placeholder="e.g. 2018671404")
        seed_utr = col_b.text_input("UTR ID", key="bs_utr_id", placeholder="e.g. 3639763")
        mp_col, alert_col = st.columns([1, 2])
        max_players = mp_col.number_input(
            "Max players to collect (includes the seed; lower it for a quick test)",
            min_value=2, max_value=99, value=min(BOOTSTRAP_MAX_PLAYERS, 99), step=1,
            key="bs_max_players",
            help="Keep this under 100 — too many requests may get you rate-limited or blocked by USTA/UTR.",
        )
        alert_col.warning(
            "⚠️ Keep this **under 100**. Too many requests may get you blocked by USTA or UTR.",
            icon="⚠️",
        )

        start = st.button("Start bootstrap", type="primary", key="bs_start", use_container_width=True)

        if start:
            if not (seed_usta.strip().isdigit() and seed_utr.strip().isdigit()):
                st.error("Both USTA ID and UTR ID must be numeric.")
                return
            from scripts.bootstrap import bootstrap_from_seed
            usta = USTAScraper()
            utr = UTRScraper()
            status = st.status("Bootstrapping mapping DB...", expanded=True)
            if not usta.check_usta_token():
                status.update(label="USTA token unavailable", state="error")
                st.error("Could not find a valid USTA match-API token. Please (re)login to USTA.")
                return
            if not utr._ensure_jwt():
                status.update(label="UTR login required", state="error")
                st.error("No UTR JWT available. Please login to UTR.")
                return
            result = bootstrap_from_seed(
                seed_usta.strip(), seed_utr.strip(),
                max_players=int(max_players),
                progress_cb=status.write, usta=usta, utr=utr,
            )
            if not result["ok"]:
                status.update(label="Bootstrap failed", state="error")
                st.error(result["error"])
            else:
                status.update(label=f"Bootstrap complete — {result['added']} players mapped",
                              state="complete", expanded=False)
                st.session_state._bootstrap_done = True
                st.session_state._bootstrap_result = result
                st.rerun()


def render_bootstrap_summary():
    """Show what the last bootstrap collected (persists for the session)."""
    result = st.session_state.get("_bootstrap_result")
    if not result or not result.get("ok"):
        return
    players = result.get("players", [])
    st.success(
        f"Mapping DB bootstrapped — **{result['added']}** players mapped "
        f"over {result['depth']} layer(s)."
    )
    if players:
        rows = [{
            "Player": p.get("name"),
            "USTA ID": p.get("usta_id"),
            "UTR ID": p.get("utr_id"),
            "Confidence": p.get("confidence"),
            "Method": p.get("match_method"),
        } for p in players]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("Bad pairs can be fixed later via **Report Wrong Mapping** after a tournament run.")


def render_user_guide():
    """Default main content shown before any tournament has been run."""
    st.subheader("How to use TennisLink")
    st.markdown(
        """
TennisLink pulls a **USTA tournament draw** and enriches each player with their
**WTN / ranking points** and **UTR rating**, then shows the combined table.

1. **Check logins** (sidebar → *Login Status* → **Check Login Status**). Log in to both
   **USTA** and **UTR**; both are required for complete data and for bootstrapping.
2. **Enter a tournament** — paste a USTA TennisLink tournament **URL or GUID** in the sidebar
   (e.g. `2506261054211502`).
3. **Pick a division** (optional) — the dropdown fills from the tournament's events
   (e.g. `Boys 18`). Leave it on *All divisions* for everyone.
4. **Run** — click **Run**. USTA profiles and UTR ratings are fetched and cached locally.
5. **Read the table** — `WTN` is the USTA World Tennis Number; the ranking column shows points
   in the selected list; `UTR` is the matched Universal Tennis Rating, with a link to each profile.
6. **Refresh All** — re-fetch only players profiled before today.
7. **Report Wrong Mapping** — fix an incorrect USTA→UTR link; your correction is authoritative.
8. **Download** — export the results as **TSV** or **Excel** from the toolbar.

> **Fresh install?** If the mapping DB is nearly empty, a **Bootstrap mapping DB** panel appears
> above. Enter one USTA↔UTR pair you already know and TennisLink will cross-check match histories
> (date + score + opponent) to build a starter mapping DB.
"""
    )



# ── Session state ──────────────────────────────────────────────────
if "last_run" not in st.session_state:
    st.session_state.last_run = None
if "force_refresh" not in st.session_state:
    st.session_state.force_refresh = set()
if "sidebar_open" not in st.session_state:
    st.session_state.sidebar_open = True
if "_usta_login" not in st.session_state:
    st.session_state._usta_login = None  # None = unchecked, True/False
if "_utr_login" not in st.session_state:
    st.session_state._utr_login = None
if "_login_checked" not in st.session_state:
    st.session_state._login_checked = False
if "_run_pending" not in st.session_state:
    st.session_state._run_pending = False
if "_saved_inputs" not in st.session_state:
    st.session_state._saved_inputs = {}
if "_bootstrap_skip" not in st.session_state:
    st.session_state._bootstrap_skip = False
if "_bootstrap_done" not in st.session_state:
    st.session_state._bootstrap_done = False
if "_bootstrap_result" not in st.session_state:
    st.session_state._bootstrap_result = None

# ── Auto-check login status on startup ──
if not st.session_state._login_checked:
    st.session_state._usta_login = check_usta_login()
    st.session_state._utr_login = check_utr_login()
    st.session_state._login_checked = True

# ── Sidebar ─────────────────────────────────────────────────────────
col_title, col_toggle = st.columns([6, 1])
with col_title:
    st.title("TennisLink")
if col_toggle.button("☰", help="Toggle sidebar"):
    st.session_state.sidebar_open = not st.session_state.sidebar_open
    st.rerun()

# Defaults for sidebar inputs (needed when sidebar closed but auto-run pending)
if "_saved_inputs" in st.session_state and st.session_state._saved_inputs:
    s = st.session_state._saved_inputs
    tournament = s.get("tournament", "")
    division = s.get("division", "")
    no_login = s.get("no_login", False)
    no_profiles = s.get("no_profiles", False)
    no_cross_ref = s.get("no_cross_ref", False)
    visible = s.get("visible", False)
else:
    tournament = division = ""
    no_login = no_profiles = no_cross_ref = False
    visible = False
run = False

if st.session_state.sidebar_open:
    with st.sidebar:
        st.header("Settings")
        tournament = st.text_input("Tournament GUID or URL", placeholder="e.g. 2506261054211502 or https://...")

        # Division dropdown populated from tournament events
        if tournament:
            div_cache_key = f"_divs_{tournament}"
            if div_cache_key not in st.session_state:
                try:
                    _s = USTAScraper()
                    st.session_state[div_cache_key] = _s.get_tournament_divisions(tournament)
                except Exception:
                    st.session_state[div_cache_key] = []
            div_opts = ["All divisions"] + sorted(st.session_state[div_cache_key])
            current_div = division if division else "All divisions"
            if current_div not in div_opts:
                current_div = "All divisions"
            division = st.selectbox("Division", options=div_opts,
                                    index=div_opts.index(current_div))
            if division == "All divisions":
                division = ""
        else:
            division = st.text_input("Division filter (optional)",
                                     placeholder="e.g. Boys U12, Girls 18",
                                     disabled=True)

        st.divider()
        st.subheader("Login Status")
        usta_ok = st.session_state._usta_login
        utr_ok = st.session_state._utr_login

        col_a, col_b = st.columns(2)
        col_a.markdown(f"**USTA:** {'✅' if usta_ok else '❌'}")
        col_b.markdown(f"**UTR:** {'✅' if utr_ok else '❌'}")
        if st.button("🔍 Check Login Status", use_container_width=True, key="ck_both"):
            with st.spinner("Checking login status..."):
                st.session_state._usta_login = check_usta_login()
                st.session_state._utr_login = check_utr_login()
            st.rerun()

        if usta_ok is False:
            if st.button("🔑 Login USTA", use_container_width=True, key="lg_usta"):
                open_login_browser("USTA")
                st.session_state._usta_login = None
                st.rerun()
        if utr_ok is False:
            if st.button("🔑 Login UTR", use_container_width=True, key="lg_utr"):
                open_login_browser("UTR")
                st.session_state._utr_login = None
                st.rerun()
            st.caption("⚠️ UTR not logged in — precise UTR ratings unavailable if uncached.")

        st.divider()
        st.subheader("Options")
        no_login = st.checkbox("Skip UTR login", value=False)
        no_profiles = st.checkbox("Skip detailed profiles", value=False)
        no_cross_ref = st.checkbox("Skip cross-reference", value=False)
        visible = st.checkbox("Visible browser", value=False)

        run = st.button("Run", type="primary", use_container_width=True)

        st.divider()
        try:
            st.caption(f"Mapping DB: {count_mappings()} player(s)")
        except Exception:
            pass

# Save sidebar inputs for auto-rerun
if run or st.session_state._run_pending:
    st.session_state._saved_inputs = {
        "tournament": tournament,
        "division": division,
        "no_login": no_login,
        "no_profiles": no_profiles,
        "no_cross_ref": no_cross_ref,
        "visible": visible,
    }

# ── Pipeline execution ──────────────────────────────────────────────
_trigger = run or st.session_state._run_pending
if _trigger:
    st.session_state._run_pending = False
    # Restore saved inputs on auto-rerun
    if not run and st.session_state._saved_inputs:
        s = st.session_state._saved_inputs
        tournament, division = s["tournament"], s["division"]
        no_login, no_profiles = s["no_login"], s["no_profiles"]
        no_cross_ref, visible = s["no_cross_ref"], s["visible"]

    if not tournament:
        st.error("Enter a tournament GUID or URL.")
        st.stop()

    headless = not visible
    run_profiles = not no_profiles

    status = st.status("Starting...", expanded=True)

    try:
        # Quick login status check
        if not check_usta_login():
            st.warning("⚠️ USTA not logged in — tournament draw may be unavailable.", icon="⚠️")
        utr_login_status = check_utr_login()
        if not utr_login_status:
            st.info("ℹ️ UTR not logged in — precise UTR scrape skipped; cached ratings still used.", icon="ℹ️")

        usta = USTAScraper()
        status.write("Fetching tournament info...")
        t_info = usta.get_tournament_info(tournament)
        status.write(f"Tournament: **{t_info['name']}**")

        # ── Step 1: Get player list (ClubSpark, no Playwright) ──
        status.write("Fetching player list from ClubSpark...")
        usta_players = usta.scrape_tournament(tournament, division, headless=headless)
        if not usta_players:
            st.error("No players found.")
            st.stop()
        status.write(f"Found **{len(usta_players)}** players.")

        # ── Step 2: Resolve USTA profiles + UTR for each player ──
        utr = UTRScraper()
        if not no_login:
            utr.login_if_needed()

        matcher = PlayerMatcher(utr)
        is_mixed = is_mixed_team_division(division)
        ranking_list_name = build_ranking_list_name(division)
        ranking_list_label = ranking_list_name or "Ranking Pts"

        resolved = []
        player_pb = st.progress(0.0)
        conn = get_connection()

        for idx, up in enumerate(usta_players):
            player_pb.progress(
                (idx + 1) / len(usta_players),
                text=f"Resolving player {idx+1}/{len(usta_players)}: {up['name']}",
            )

            usta_id = up["usta_id"]
            force = usta_id in st.session_state.force_refresh

            # ── USTA profile: check DB cache first ──
            usta_cached = None
            if run_profiles:
                row = conn.execute(
                    "SELECT * FROM usta_player_profiles WHERE usta_id = ?", (usta_id,)
                ).fetchone()
                if row:
                    usta_cached = dict(row)
                    # Re-fetch if cached wtn_singles is None but API metadata exists
                    # (indicates stale cache from a prior scrape that missed the value)
                    if usta_cached.get("wtn_singles") is None and usta_cached.get("wtn_singles_confidence") is not None:
                        usta_cached = None
                    # On force refresh, only keep cache if profiled today
                    elif force and not _updated_today(usta_cached.get("last_updated")):
                        usta_cached = None

            if usta_cached:
                up["wtn_singles"] = usta_cached.get("wtn_singles")
                up["wtn_doubles"] = usta_cached.get("wtn_doubles")
                up["section"] = usta_cached.get("section")
                up["district"] = usta_cached.get("district")
                up["gender"] = usta_cached.get("gender")
                ranking_json = usta_cached.get("ranking_json")
                if ranking_json:
                    up["rankings"] = json.loads(ranking_json)
                else:
                    ranking_rows = conn.execute(
                        "SELECT * FROM usta_player_rankings WHERE usta_id = ?", (usta_id,)
                    ).fetchall()
                    up["rankings"] = [dict(r) for r in ranking_rows]
                usta_ts = usta_cached.get("last_updated", "")
            elif run_profiles:
                profile = usta.fetch_player_profile(usta_id, headless=headless)
                if profile:
                    up.update(profile)
                    save_usta_player_history(usta_id, profile)
                usta_ts = datetime.datetime.now().isoformat()
            else:
                usta_ts = ""

            player_gender = (up.get("gender") or "").upper()
            points_list = ranking_list_name
            if is_mixed:
                points_list = build_ranking_list_name(division, player_gender)
            points = get_points_for_list(up.get("rankings", []), points_list)

            # ── UTR profile ──
            match_res = matcher.find_utr_profile(up, usta_players, no_cross_ref=no_cross_ref)

            utr_ts = ""
            utr_id = None
            utr_singles = None
            utr_doubles = None

            if match_res:
                utr_id = match_res["utr_id"]

                # Try cache first (skip 0.0 and integer-round = obfuscated)
                utr_row = conn.execute(
                    "SELECT * FROM utr_player_profiles WHERE utr_id = ?", (utr_id,)
                ).fetchone()
                if utr_row:
                    uc = dict(utr_row)
                    utr_ts = uc.get("last_updated", "")
                    cached_singles = uc.get("utr_singles")
                    use_cache = not force or _updated_today(utr_ts)
                    if use_cache and cached_singles is not None and cached_singles != 0.0 and cached_singles != int(cached_singles):
                        utr_singles = cached_singles
                        utr_doubles = uc.get("utr_doubles")
                else:
                    utr_ts = ""

                # If still no valid rating (or obfuscated integer), try API
                if utr_singles is None or utr_singles == 0.0:
                    profile = utr.get_player_profile(utr_id)

                    # UTR profile ID no longer exists -> automatically find the new ID
                    if profile is None and utr.last_profile_not_found:
                        status.write(f"⚠️ UTR ID {utr_id} for **{up['name']}** no longer exists — searching for updated ID...")
                        new_match = matcher.rematch_player(
                            up, usta_players,
                            no_cross_ref=no_cross_ref,
                            exclude_utr_ids={str(utr_id)},
                        )
                        if new_match and new_match.get("utr_id"):
                            old_utr_id = utr_id
                            utr_id = str(new_match["utr_id"])
                            status.write(f"✅ New UTR ID found for **{up['name']}**: {old_utr_id} → {utr_id}")
                            profile = utr.get_player_profile(utr_id)

                    if profile:
                        utr_singles = profile.get("utr_singles")
                        utr_doubles = profile.get("utr_doubles")
                        utr_ts = datetime.datetime.now().isoformat()
                        save_utr_player_history(utr_id, profile)
                    elif utr.last_profile_not_found:
                        status.write(f"⚠️ Could not find a replacement UTR profile for **{up['name']}**.")

            gender_display = "F" if player_gender in ("F", "FEMALE") else "M" if player_gender in ("M", "MALE") else ""

            record = {
                "Player Name": up["name"],
                "Hometown": f"{up.get('city') or ''}, {up.get('state') or ''}".strip(", "),
                "USTA ID": up["usta_id"],
                "Gender": gender_display,
                "WTN": _val(up.get("wtn_singles")),
                ranking_list_label: _val(points),
                "Profile Updated": usta_ts,
                "UTR ID": _val(utr_id),
                "UTR": _val(utr_singles),
                "UTR Updated": utr_ts,
            }
            resolved.append(record)

        conn.close()

        # If any players still have 0.0 (obfuscated) UTR, try fresh API call with JWT
        if utr_login_status:
            usta_lookup = {p["usta_id"]: p for p in usta_players}
            for rec in resolved:
                uid = rec["UTR ID"]
                if uid in ("N/A", None, ""):
                    continue
                try:
                    val = float(rec["UTR"])
                except (ValueError, TypeError):
                    val = None
                if val is None or val == 0.0:
                    profile = utr.get_player_profile(uid)

                    # Stale UTR ID -> auto re-match and update the record
                    if profile is None and utr.last_profile_not_found:
                        up_ref = usta_lookup.get(str(rec["USTA ID"]))
                        if up_ref:
                            new_match = matcher.rematch_player(
                                up_ref, usta_players,
                                no_cross_ref=no_cross_ref,
                                exclude_utr_ids={str(uid)},
                            )
                            if new_match and new_match.get("utr_id"):
                                rec["UTR ID"] = str(new_match["utr_id"])
                                profile = utr.get_player_profile(rec["UTR ID"])

                    if profile and profile.get("utr_singles") is not None and profile["utr_singles"] != 0.0:
                        rec["UTR"] = profile["utr_singles"]
                        rec["UTR Updated"] = datetime.datetime.now().isoformat()

        st.session_state.force_refresh = set()
        df = pd.DataFrame(resolved)
        col_order = ["Player Name", "Hometown", "USTA ID"]
        if is_mixed:
            col_order.append("Gender")
        col_order += ["WTN", ranking_list_label, "Profile Updated",
                      "UTR ID", "UTR", "UTR Updated"]
        df = df[col_order]

        name = t_info["name"]
        guid = t_info["guid"][:8]
        date_raw = t_info.get("start_date", "")
        yyyymmdd = date_raw[:10].replace("-", "") if date_raw else "unknown"
        safe_name = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_")
        safe_div = re.sub(r"[^a-zA-Z0-9]+", "_", division).strip("_") if division else "All"
        fname = f"Tournament.{yyyymmdd}.{safe_name}.{guid}.{safe_div}.tsv"
        os.makedirs(DATA_DIR, exist_ok=True)
        out_path = os.path.join(DATA_DIR, fname)
        df.to_csv(out_path, index=False, sep="\t")

        status.update(label="Complete!", state="complete", expanded=False)

        st.session_state.last_run = {
            "df": df,
            "fname": fname,
            "out_path": out_path,
            "usta_ids": df["USTA ID"].tolist(),
            "ranking_label": ranking_list_label,
        }
        st.session_state._active_tab = "🎾 Tournament Players"
        st.rerun()

    except Exception as e:
        status.update(label="Error", state="error")
        st.exception(e)

# ── Main tabs ───────────────────────────────────────────────────────
TAB_GUIDE = "📖 User Guide"
TAB_TOURNAMENT = "🎾 Tournament Players"
TAB_BOOTSTRAP = "🧩 Bootstrap Pairs"
_TABS = [TAB_GUIDE, TAB_TOURNAMENT, TAB_BOOTSTRAP]

try:
    _mapping_count = count_mappings()
except Exception:
    _mapping_count = 0

if "_active_tab" not in st.session_state or st.session_state._active_tab not in _TABS:
    st.session_state._active_tab = (
        TAB_BOOTSTRAP
        if (_mapping_count < BOOTSTRAP_MIN_PLAYERS and not st.session_state._bootstrap_done)
        else TAB_GUIDE
    )

st.session_state._active_tab = st.radio(
    "Navigation", _TABS,
    index=_TABS.index(st.session_state._active_tab),
    horizontal=True, label_visibility="collapsed",
)

# ── Display results ─────────────────────────────────────────────────
if st.session_state._active_tab == TAB_GUIDE:
    render_user_guide()
elif st.session_state._active_tab == TAB_BOOTSTRAP:
    render_bootstrap_tab()
elif st.session_state.last_run is not None:
    df = st.session_state.last_run["df"]
    fname = st.session_state.last_run["fname"]
    out_path = st.session_state.last_run["out_path"]

    # ── Toolbar ──
    col_a, col_b, col_c, _ = st.columns([1.5, 1, 1, 6])
    if col_a.button("⟳ Refresh All", type="secondary"):
        st.session_state.force_refresh = set(st.session_state.last_run["usta_ids"])
        st.session_state._run_pending = True
        st.rerun()

    tsv_buf = io.BytesIO()
    df.to_csv(tsv_buf, index=False, sep="\t", encoding="utf-8")
    col_b.download_button("Download TSV", data=tsv_buf.getvalue(),
                          file_name=fname, mime="text/tab-separated-values")

    xlsx_buf = io.BytesIO()
    with pd.ExcelWriter(xlsx_buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Results")
    col_c.download_button("Download Excel", data=xlsx_buf.getvalue(),
                          file_name=fname.replace(".tsv", ".xlsx"),
                          mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # ── Sortable dataframe ──
    st.subheader("Results")
    display_df = df.copy()
    display_df["USTA Profile"] = display_df["USTA ID"].apply(
        lambda x: f"https://www.usta.com/en/home/play/player-search/profile.html#?uaid={x}"
    )
    display_df["UTR Profile"] = display_df["UTR ID"].apply(
        lambda x: f"https://app.utrsports.net/profiles/{x}" if x != "N/A" else ""
    )
    rl = st.session_state.last_run.get("ranking_label", "Ranking Pts")
    for col in ["WTN", rl, "UTR"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].replace("N/A", None)

    def _fmt_ts(val):
        if val and val != "N/A":
            try:
                return datetime.datetime.fromisoformat(val).strftime("%m-%d")
            except Exception:
                pass
        return ""

    display_df["Profile Updated"] = df["Profile Updated"].apply(_fmt_ts)
    display_df["UTR Updated"] = df["UTR Updated"].apply(_fmt_ts)

    # Sort by ranking points descending by default (N/A at bottom)
    if rl in display_df.columns:
        display_df["_rank_sort"] = pd.to_numeric(display_df[rl].replace("N/A", None), errors="coerce")
        display_df = display_df.sort_values("_rank_sort", ascending=False, na_position="last")
        display_df = display_df.drop(columns=["_rank_sort"])

    # Gender filter (only shown when Gender column present)
    if "Gender" in display_df.columns:
        gender_vals = sorted([g for g in display_df["Gender"].dropna().unique() if g])
        gender_opts = ["All"] + gender_vals
        gender_filter = st.radio("Gender", options=gender_opts, index=0,
                                 horizontal=True, key="gender_filter")
        if gender_filter != "All":
            display_df = display_df[display_df["Gender"] == gender_filter]

    display_df = display_df.reset_index(drop=True)
    display_df.insert(0, "#", display_df.index + 1)

    col_config = {
        "#": st.column_config.TextColumn("#", width="small"),
        "Player Name": st.column_config.TextColumn("Player Name", width="medium"),
        "Hometown": st.column_config.TextColumn("Hometown", width="medium"),
        "USTA ID": st.column_config.TextColumn("USTA ID", width="small"),
        "Gender": st.column_config.TextColumn("Gender", width="small"),
        "USTA Profile": st.column_config.LinkColumn("USTA", width="small",
            display_text=r"uaid=(\d+)$"),
        "WTN": st.column_config.NumberColumn("WTN", width="small", format="%.1f"),
        "Profile Updated": st.column_config.TextColumn("Profiled", width="small"),
        "UTR Profile": st.column_config.LinkColumn("UTR", width="small",
            display_text=r"profiles/(\d+)$"),
        "UTR": st.column_config.NumberColumn("UTR Rtng", width="small", format="%.2f"),
        "UTR Updated": st.column_config.TextColumn("UTR Profiled", width="small"),
    }
    if rl in display_df.columns:
        col_config[rl] = st.column_config.NumberColumn(rl, width="small", format="%d",
            help="Points in the selected ranking list")

    base_order = ["#", "Player Name"]
    if "Gender" in display_df.columns:
        base_order.append("Gender")
    base_order += ["WTN"]
    if rl in display_df.columns:
        base_order.append(rl)
    base_order += ["Profile Updated", "UTR", "UTR Updated",
                   "Hometown", "USTA Profile", "UTR Profile"]

    st.dataframe(
        display_df,
        column_config=col_config,
        column_order=base_order,
        hide_index=True,
        width="stretch",
    )

    # ── Report wrong mapping & correct manually ──
    st.divider()
    st.subheader("Report Wrong Mapping")
    player_map = {}
    for _, row in df.iterrows():
        label = f"{row['Player Name']} (USTA {row['USTA ID']}) — UTR {row['UTR ID']}"
        player_map[label] = {"usta_id": str(row["USTA ID"]), "utr_id": str(row["UTR ID"])}
    col_sel, col_id, col_btn = st.columns([2, 1, 1])
    selected_label = col_sel.selectbox("Select player", options=list(player_map.keys()),
                                       key="fix_player", label_visibility="collapsed")
    current_utr = player_map[selected_label]["utr_id"]
    correct_utr = col_id.text_input("Correct UTR ID", placeholder="e.g. 5354648",
                                    value="" if current_utr == "N/A" else current_utr,
                                    key="fix_utr_id")
    if col_btn.button("Apply Correction", type="primary", use_container_width=True, key="apply_fix"):
        new_utr = correct_utr.strip()
        if not new_utr.isdigit():
            st.error("Please enter a valid numeric UTR ID.")
        else:
            usta_id = player_map[selected_label]["usta_id"]
            try:
                save_mapping(usta_id, new_utr, match_method="manual_correct", confidence=1.0)
                _c = UTRScraper()
                _prof = _c.get_player_profile(new_utr)
                if _prof:
                    st.success(f"Mapping updated: USTA {usta_id} → UTR {new_utr} "
                               f"({_prof.get('name')}, S={_prof.get('utr_singles')}). Rerunning...")
                else:
                    st.success(f"Mapping updated: USTA {usta_id} → UTR {new_utr}. Rerunning...")
                st.session_state.force_refresh = set()
                st.session_state._run_pending = True
                st.rerun()
            except Exception as ex:
                st.error(f"Failed to apply correction: {ex}")

    st.info(f"Report saved to `{out_path}`")

else:
    st.info(
        "No tournament loaded yet. Open the sidebar, paste a USTA tournament URL/GUID, "
        "then click **Run**."
    )
