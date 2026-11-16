import os
import re
import io
import json
import math
import sqlite3
import datetime
import pandas as pd
import streamlit as st

from scripts.service.render import (build_results_html, short_ranking_label,
                            tournament_header_html, format_duration,
                            order_checked_events)
from scripts.service.tournament_finder import (JUNIOR_LEVELS, search_tournaments,
                                       fetch_tournament_events, event_tooltip,
                                       tournament_sort_key)
from scripts.service.event_runner import run_event, run_division

from scripts.service.config import (UTR_USER, UTR_PASS, DB_PATH,
                            BOOTSTRAP_MIN_PLAYERS, BOOTSTRAP_MAX_PLAYERS, BOOTSTRAP_MAX_DEPTH)
from scripts.service.usta_scraper import USTAScraper
from scripts.service.utr_scraper import UTRScraper
from scripts.service.matcher import (PlayerMatcher, WTN_UTR_INTERCEPT, WTN_UTR_SLOPE,
                             WTN_UTR_RESID_SD, RATING_MIN_COMPATIBILITY)
from scripts.service.db import (get_connection, save_mapping, save_usta_player_history,
                        save_utr_player_history, count_mappings, get_checked_events,
                        get_checked_event, save_checked_event)

st.set_page_config(page_title="TennisLink", layout="wide", initial_sidebar_state="expanded")

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tournaments")
USTA_PROFILE_URL = "https://www.usta.com/en/home/play/player-search/profile.html#?uaid={}"
UTR_PROFILE_URL = "https://app.utrsports.net/profiles/{}"


def check_usta_login() -> bool:
    """Check if the persistent Playwright context has a valid www.usta.com session."""
    from playwright.sync_api import sync_playwright
    from scripts.service.utr_scraper import PLAYWRIGHT_USER_DIR
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
    from scripts.service.utr_scraper import PLAYWRIGHT_USER_DIR
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
    from scripts.service.utr_scraper import PLAYWRIGHT_USER_DIR
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
            from scripts.service.bootstrap import bootstrap_from_seed
            usta = USTAScraper()
            utr = UTRScraper()
            status = st.status("Bootstrapping mapping DB...", expanded=True)
            if UTRScraper.is_rate_limited():
                status.update(label="UTR rate-limited", state="error")
                st.error(
                    "UTR is rate-limiting. Please wait "
                    f"~{format_duration(UTRScraper.rate_limit_remaining())} before bootstrapping."
                )
                return
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
2. **Open the 🎾 Tournament Players tab** and paste a USTA TennisLink tournament **URL or GUID**
   (e.g. `2506261054211502`).
3. **Pick a division** (optional) — the dropdown fills from the tournament's events
   (e.g. `Boys 18`). Leave it on *All divisions* for everyone.
4. **Run** — click **Run** in that tab. USTA profiles and UTR ratings are fetched and cached locally.
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

    st.divider()
    render_rating_band_chart()


def render_rating_band_chart():
    """Plot the plausible WTN <-> UTR band used to reject implausible matches."""
    st.subheader("WTN ↔ UTR match band")
    st.caption(
        f"Empirical mapping: UTR ≈ {WTN_UTR_INTERCEPT:.2f} "
        f"{WTN_UTR_SLOPE:+.4f} × WTN (residual sd ≈ {WTN_UTR_RESID_SD:.2f}), "
        f"fit on local mapped pairs whose WTN and UTR were profiled within 7 days. "
        f"Shaded = plausible band; it is a soft tie-breaker when ranking candidates."
    )

    wtn_vals = list(range(1, 41))
    expected = [max(1.0, min(16.5, WTN_UTR_INTERCEPT + WTN_UTR_SLOPE * w)) for w in wtn_vals]
    z_limit = math.sqrt(-2.0 * math.log(RATING_MIN_COMPATIBILITY))
    half = z_limit * WTN_UTR_RESID_SD
    band = pd.DataFrame({
        "WTN": wtn_vals,
        "expected": expected,
        "lower": [max(1.0, e - half) for e in expected],
        "upper": [min(16.5, e + half) for e in expected],
    })

    sample = None
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT u.wtn_singles AS wtn, r.utr_singles AS utr
            FROM player_mappings m
            JOIN usta_player_profiles u ON u.usta_id = m.usta_id
            JOIN utr_player_profiles r ON r.utr_id = m.utr_id
            WHERE u.wtn_singles IS NOT NULL AND u.wtn_singles > 0
              AND r.utr_singles IS NOT NULL AND r.utr_singles > 0
              AND u.last_updated IS NOT NULL AND r.last_updated IS NOT NULL
              AND ABS(julianday(u.last_updated) - julianday(r.last_updated)) <= 7
            """
        ).fetchall()
        conn.close()
        if rows:
            sample = pd.DataFrame([dict(r) for r in rows])
    except Exception:
        sample = None

    try:
        import altair as alt
        x_scale = alt.Scale(domain=[1, 40])
        y_scale = alt.Scale(domain=[0, 17])
        x_enc = alt.X("WTN:Q", title="WTN (lower is better)", scale=x_scale)
        area = alt.Chart(band).mark_area(opacity=0.15, color="#4C78A8").encode(
            x=x_enc,
            y=alt.Y("lower:Q", title="UTR (higher is better)", scale=y_scale),
            y2="upper:Q",
        )
        line = alt.Chart(band).mark_line(color="#4C78A8", strokeWidth=2).encode(
            x=x_enc, y=alt.Y("expected:Q", scale=y_scale),
        )
        layers = [area, line]
        if sample is not None and not sample.empty:
            layers.append(
                alt.Chart(sample).mark_circle(size=18, opacity=0.3, color="#F58518").encode(
                    x=alt.X("wtn:Q", scale=x_scale),
                    y=alt.Y("utr:Q", scale=y_scale),
                    tooltip=[alt.Tooltip("wtn:Q", title="WTN"), alt.Tooltip("utr:Q", title="UTR")],
                )
            )
        st.altair_chart(alt.layer(*layers).properties(height=360), use_container_width=True)
        if sample is not None and not sample.empty:
            st.caption(f"Orange dots: {len(sample)} local mapped pairs (WTN vs UTR), profiled within 7 days.")
    except Exception:
        st.line_chart(band.set_index("WTN")[["lower", "expected", "upper"]])
        if sample is not None and not sample.empty:
            st.scatter_chart(sample, x="wtn", y="utr")


def render_results_table(display_df, col_order, ranking_label):
    st.html(build_results_html(display_df, col_order, ranking_label))


def render_players_form():
    """Tournament Players settings live in the tab (not the global sidebar)."""
    st.subheader("🎾 Tournament Players")
    saved = st.session_state.get("_saved_inputs") or {}

    tournament = st.text_input(
        "Tournament GUID or URL",
        value=saved.get("tournament", ""),
        placeholder="e.g. 2506261054211502 or https://...",
        key="players_tournament",
    )

    division = ""
    if tournament:
        cache_key = f"_divs_{tournament}"
        if cache_key not in st.session_state:
            try:
                st.session_state[cache_key] = USTAScraper().get_tournament_divisions(tournament)
            except Exception:
                st.session_state[cache_key] = []
        opts = ["All divisions"] + sorted(st.session_state[cache_key])
        current = saved.get("division") or "All divisions"
        if current not in opts:
            current = "All divisions"
        division = st.selectbox("Division", opts, index=opts.index(current),
                                key="players_division")
        if division == "All divisions":
            division = ""

    with st.expander("Run options", expanded=False):
        no_login = st.checkbox("Skip UTR login", key="players_no_login")
        no_profiles = st.checkbox("Skip detailed profiles", key="players_no_profiles")
        no_cross_ref = st.checkbox("Skip cross-reference", key="players_no_cross_ref")
        visible = st.checkbox("Visible browser", key="players_visible")

    if st.button("Run", type="primary", key="players_run"):
        if not tournament.strip():
            st.error("Enter a tournament GUID or URL.")
        else:
            st.session_state._saved_inputs = {
                "tournament": tournament.strip(),
                "division": division,
                "no_login": no_login,
                "no_profiles": no_profiles,
                "no_cross_ref": no_cross_ref,
                "visible": visible,
            }
            st.session_state._run_pending = True
            st.rerun()


def render_results_panel(df, fname, ranking_label, key_prefix, out_path=None,
                         on_refresh=None, on_correct=None):
    """Shared results view: toolbar, sortable pinned table, downloads, correction."""
    if on_refresh:
        c_ref, c_tsv, c_xlsx, _ = st.columns([1.5, 1, 1, 5])
        if c_ref.button("⟳ Refresh All", type="secondary", key=f"{key_prefix}_refresh"):
            on_refresh()
            st.rerun()
        tsv_col, xlsx_col = c_tsv, c_xlsx
    else:
        c_tsv, c_xlsx, _ = st.columns([1, 1, 5])
        tsv_col, xlsx_col = c_tsv, c_xlsx

    tsv_buf = io.BytesIO()
    df.to_csv(tsv_buf, index=False, sep="\t", encoding="utf-8")
    tsv_col.download_button("Download TSV", data=tsv_buf.getvalue(), file_name=fname,
                            mime="text/tab-separated-values",
                            key=f"{key_prefix}_dl_tsv")

    xlsx_buf = io.BytesIO()
    with pd.ExcelWriter(xlsx_buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Results")
    xlsx_col.download_button(
        "Download Excel", data=xlsx_buf.getvalue(),
        file_name=fname.replace(".tsv", ".xlsx"),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"{key_prefix}_dl_xlsx",
    )

    st.subheader("Results")
    if "New" in df.columns:
        nnew = int((df["New"].astype(str) == "NEW").sum())
        if nnew:
            st.success(f"{nnew} new player(s) since the last check.")
    display_df = df.copy()
    display_df["USTA Profile"] = display_df["USTA ID"].apply(
        lambda x: f"https://www.usta.com/en/home/play/player-search/profile.html#?uaid={x}"
    )
    display_df["UTR Profile"] = display_df["UTR ID"].apply(
        lambda x: f"https://app.utrsports.net/profiles/{x}" if x != "N/A" else ""
    )
    for col in ["WTN", ranking_label, "UTR"]:
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

    if "Gender" in display_df.columns:
        gender_vals = sorted([g for g in display_df["Gender"].dropna().unique() if g])
        gender_opts = ["All"] + gender_vals
        gender_filter = st.radio("Gender", options=gender_opts, index=0, horizontal=True,
                                 key=f"{key_prefix}_gender")
        if gender_filter != "All":
            display_df = display_df[display_df["Gender"] == gender_filter]

    numeric_cols = [c for c in ["WTN", ranking_label, "UTR"] if c in display_df.columns]
    sort_options = []
    for c in [ranking_label, "WTN", "UTR", "Status", "Player Name", "Hometown"]:
        if c in display_df.columns and c not in sort_options:
            sort_options.append(c)
    if not sort_options:
        sort_options = ["Player Name"]

    s_col, o_col = st.columns([1, 1])
    sort_choice = s_col.selectbox("Sort by", sort_options, index=0,
                                  key=f"{key_prefix}_sort")
    order = o_col.radio("Order", ["Descending", "Ascending"], horizontal=True,
                        key=f"{key_prefix}_order")
    ascending = order == "Ascending"
    st.caption("The **#** column is pinned left and numbered 1..N for the current **Sort by** order.")

    if sort_choice in numeric_cols:
        display_df["_sort_key"] = pd.to_numeric(display_df[sort_choice], errors="coerce")
        display_df = display_df.sort_values("_sort_key", ascending=ascending, na_position="last")
        display_df = display_df.drop(columns=["_sort_key"])
    else:
        display_df = display_df.sort_values(
            sort_choice, ascending=ascending, na_position="last",
            key=lambda s: s.astype(str).str.lower(),
        )

    display_df = display_df.reset_index(drop=True)
    display_df.insert(0, "#", display_df.index + 1)

    base_order = ["#", "Player Name"]
    if "New" in display_df.columns:
        base_order.append("New")
    base_order.append("Status")
    if "Gender" in display_df.columns:
        base_order.append("Gender")
    base_order += ["WTN"]
    if ranking_label in display_df.columns:
        base_order.append(ranking_label)
    base_order += ["Profile Updated", "UTR", "UTR Updated",
                   "Hometown", "USTA Profile", "UTR Profile"]

    render_results_table(display_df, base_order, ranking_label)

    if on_correct is not None:
        st.divider()
        st.subheader("Report Wrong Mapping")
        player_map = {}
        for _, row in df.iterrows():
            label = f"{row['Player Name']} (USTA {row['USTA ID']}) — UTR {row['UTR ID']}"
            player_map[label] = {"usta_id": str(row["USTA ID"]),
                                 "utr_id": str(row["UTR ID"])}
        if player_map:
            col_sel, col_id, col_btn = st.columns([2, 1, 1])
            selected_label = col_sel.selectbox("Select player", options=list(player_map.keys()),
                                               key=f"{key_prefix}_fix_player",
                                               label_visibility="collapsed")
            current_utr = player_map[selected_label]["utr_id"]
            correct_utr = col_id.text_input(
                "Correct UTR ID", placeholder="e.g. 5354648",
                value="" if current_utr == "N/A" else current_utr,
                key=f"{key_prefix}_fix_utr",
            )
            if col_btn.button("Apply Correction", type="primary", use_container_width=True,
                              key=f"{key_prefix}_apply_fix"):
                new_utr = correct_utr.strip()
                if not new_utr.isdigit():
                    st.error("Please enter a valid numeric UTR ID.")
                else:
                    on_correct(player_map[selected_label]["usta_id"], new_utr)

    if out_path:
        st.info(f"Report saved to `{out_path}`")


# ── Tournament Finder ──────────────────────────────────────────────
FINDER_DEFAULT_INCLUDE = {"Level 7", "Level 6", "Level 5 Open", "Level 5 Closed"}
FINDER_DEFAULT_RADIUS = {
    "Level 1": 100, "Level 2": 100,
    "Level 3 Open": 100, "Level 3 Closed": 100,
    "Level 4 Open": 60, "Level 4 Closed": 60,
    "Level 5 Open": 40, "Level 5 Closed": 40,
    "Level 6": 25, "Level 7": 15,
}


def _fmt_short_date(value):
    if not value:
        return ""
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%b %d, %Y")
    except Exception:
        return str(value)[:10]


def _fmt_date_range(start, end):
    s = (start or "")[:10]
    e = (end or "")[:10]
    if s and e and s != e:
        return f"{_fmt_short_date(s)} – {_fmt_short_date(e)}"
    return _fmt_short_date(s or e)


def _sorted_events(evs):
    return sorted(evs.items(), key=lambda kv: (kv[1].get("label") or "").lower())


def render_event_result(res, item):
    """Render one result in the shared panel; correction re-runs by type."""
    def _correct(usta_id, new_utr):
        save_mapping(usta_id, new_utr, match_method="manual_correct", confidence=1.0)
        try:
            UTRScraper().get_player_profile(new_utr)
        except Exception:
            pass
        with st.spinner("Updating mapping and re-running..."):
            if item.get("kind") == "division":
                new_res = run_division(item["tid"], item.get("division", ""),
                                       progress=lambda m: None)
            else:
                new_res = run_event(item["tid"], item.get("event") or {},
                                    progress=lambda m: None)
        st.session_state._finder_runs[item["key"]] = new_res
        st.rerun()

    render_results_panel(
        res["df"], res.get("fname", "report.tsv"), res["ranking_label"],
        key_prefix=f"finder_{item['key_slug']}", out_path=res.get("out_path"),
        on_refresh=None, on_correct=_correct,
    )


def render_checked_events_panel():
    """One unified history stream (event checks + tournament runs), date-sorted."""
    try:
        events = get_checked_events()
    except Exception:
        return
    if not events:
        return

    with st.expander(f"Previously checked events ({len(events)})", expanded=False):
        include_past = st.checkbox("Include past events", value=False,
                                   key="finder_history_past")
        ordered = order_checked_events(events, include_past=include_past)
        if not ordered:
            st.caption("No upcoming events. Tick 'Include past events' to see older checks.")
            return

        labels = {}
        for e in ordered:
            day = (e.get("event_date") or "no date")[:10]
            checked = (e.get("last_checked") or "")[:10]
            kind = "Event" if e.get("event_id") else "Tournament"
            label = (f"[{kind}] {day} · {e.get('tournament_name') or e.get('tournament_id')} — "
                     f"{e.get('event_label') or e.get('event_id')} "
                     f"(checked {checked}, {e.get('player_count')} players)")
            labels[label] = e

        chosen = st.multiselect("Select checks to re-run", list(labels.keys()),
                                key="finder_history_select")
        if st.button("Re-run selected", key="finder_history_run"):
            if not chosen:
                st.warning("Select at least one.")
            else:
                usta = USTAScraper()
                utr = UTRScraper()
                runs = {}
                picked = []
                status = st.status("Re-running selected...", expanded=True)
                for i, label in enumerate(chosen):
                    e = labels[label]
                    tid = e.get("tournament_id")
                    if e.get("event_id"):
                        ev = {"id": e.get("event_id"), "label": e.get("event_label"),
                              "division": e.get("division") or {}, "level": e.get("level")}
                        status.write(f"[{i+1}/{len(chosen)}] {label}")
                        res = run_event(tid, ev, progress=status.write, usta=usta, utr=utr)
                        key = (tid, e.get("event_id"))
                        item = {"kind": "event", "tid": tid, "key": key,
                                "key_slug": f"{tid}_{e.get('event_id')}",
                                "name": e.get("tournament_name"),
                                "label": e.get("event_label"),
                                "event": ev, "division": ""}
                    else:
                        division_label = e.get("event_label") or "All divisions"
                        division = "" if division_label in ("All divisions",) else division_label
                        status.write(f"[{i+1}/{len(chosen)}] {label}")
                        res = run_division(tid, division, progress=status.write,
                                           usta=usta, utr=utr)
                        key = (tid, f"division:{division_label}")
                        item = {"kind": "division", "tid": tid, "key": key,
                                "key_slug": f"{tid}_div_{re.sub(r'[^A-Za-z0-9]+', '_', division_label)}",
                                "name": e.get("tournament_name"),
                                "label": division_label,
                                "event": None, "division": division}
                    runs[key] = res
                    picked.append(item)
                    if res.get("ok"):
                        newc = res.get("new_count") or 0
                        suffix = f", {newc} new" if newc else ""
                        status.write(f"  -> {len(res['df'])} players{suffix}")
                    else:
                        status.write(f"  x {res.get('error')}")
                st.session_state._finder_runs = runs
                st.session_state._finder_selected = picked
                status.update(label="Done", state="complete", expanded=False)
                st.rerun()


def render_finder_tab():
    st.subheader("Tournament Finder")
    st.caption("Search USTA junior tournaments by ZIP; each level has its own radius.")

    results = sorted(st.session_state.get("_finder_results") or [], key=tournament_sort_key)

    with st.expander("Search configuration", expanded=not bool(results)):
        location = st.text_input(
            "Location (ZIP or City, State)",
            value=st.session_state.get("_finder_location", "92130, CA"),
            key="finder_location",
        )

        today = datetime.date.today()
        d1, d2 = st.columns(2)
        date_from = d1.date_input(
            "From date", value=st.session_state.get("_finder_date_from", today),
            key="finder_date_from",
        )
        date_to = d2.date_input(
            "To date",
            value=st.session_state.get("_finder_date_to", today + datetime.timedelta(days=45)),
            key="finder_date_to",
        )

        st.markdown("**Levels & radius (miles)**")
        level_radius = {}
        for lvl in JUNIOR_LEVELS:
            c0, c1, c2 = st.columns([0.35, 3, 1])
            inc = c0.checkbox(
                "", value=(lvl["name"] in FINDER_DEFAULT_INCLUDE),
                key=f"finder_inc_{lvl['id']}", label_visibility="collapsed",
            )
            c1.markdown(lvl["name"])
            rad = c2.number_input(
                "", min_value=0, max_value=500, step=5,
                value=FINDER_DEFAULT_RADIUS.get(lvl["name"], 100),
                key=f"finder_rad_{lvl['id']}", label_visibility="collapsed",
            )
            if inc and rad > 0:
                level_radius[lvl["id"]] = int(rad)

        if st.button("Search tournaments", type="primary", key="finder_search_btn"):
            if not location.strip():
                st.error("Enter a location.")
            elif not level_radius:
                st.error("Include at least one level with a radius > 0.")
            elif date_to < date_from:
                st.error("The end date must be on or after the start date.")
            else:
                st.session_state._finder_location = location.strip()
                st.session_state._finder_date_from = date_from
                st.session_state._finder_date_to = date_to
                iso_from = datetime.datetime.combine(
                    date_from, datetime.time.min).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                iso_to = datetime.datetime.combine(
                    date_to, datetime.time.max).strftime("%Y-%m-%dT%H:%M:%S.999Z")
                with st.spinner("Searching USTA tournaments..."):
                    try:
                        results = search_tournaments(
                            location.strip(), level_radius,
                            date_from=iso_from, date_to=iso_to,
                        )
                    except Exception as e:
                        st.error(f"Search failed: {e}")
                        results = []
                if results:
                    details = {}
                    prog = st.progress(0.0, text="Loading event details...")
                    for i, t in enumerate(results):
                        prog.progress(
                            (i + 1) / len(results),
                            text=f"Loading event details {i+1}/{len(results)}: {t['name'][:40]}",
                        )
                        try:
                            details[t["id"]] = fetch_tournament_events(t["id"])
                        except Exception:
                            details[t["id"]] = {}
                    st.session_state._finder_results = results
                    st.session_state._finder_details = details
                    st.session_state._finder_runs = {}
                    st.session_state._finder_selected = []
                else:
                    st.session_state._finder_results = []
                    st.session_state._finder_details = {}
                    st.session_state._finder_runs = {}
                    st.session_state._finder_selected = []
                    st.warning("No tournaments found.")
                st.rerun()
    details = st.session_state.get("_finder_details") or {}

    render_checked_events_panel()

    if results:
        st.divider()
        st.write(f"**{len(results)} tournaments** (sorted by date, level, then distance)")
        st.caption("Level has a distinct color; distance is green (near) → red (far).")

        for t in results:
            evs = details.get(t["id"]) or {}
            st.markdown(
                tournament_header_html(t, _fmt_date_range(t.get("start"), t.get("end"))),
                unsafe_allow_html=True,
            )
            with st.expander(f"Events ({len(evs)})", expanded=False):
                st.caption(
                    f"{t['city']}, {t['state']} • entries close "
                    f"{_fmt_short_date(t.get('entry_deadline'))}"
                )
                if t.get("url"):
                    st.markdown(f"[Open on USTA]({t['url']})")
                if not evs:
                    st.caption("No event details available.")
                for eid, ev in _sorted_events(evs):
                    st.checkbox(
                        ev.get("label") or eid,
                        key=f"finder_sel_{t['id']}_{eid}",
                        help=event_tooltip(ev),
                    )

        if st.button("Run selected", type="primary", key="finder_run_btn"):
            selected = []
            for t in results:
                for eid, ev in _sorted_events(details.get(t["id"]) or {}):
                    if st.session_state.get(f"finder_sel_{t['id']}_{eid}"):
                        selected.append((t, ev))
            if not selected:
                st.warning("Select at least one event.")
            else:
                usta = USTAScraper()
                utr = UTRScraper()
                runs = {}
                status = st.status("Running selected events...", expanded=True)
                for i, (t, ev) in enumerate(selected):
                    status.write(f"[{i+1}/{len(selected)}] {t['name']} — {ev.get('label')}")
                    res = run_event(t["id"], ev, progress=status.write, usta=usta, utr=utr)
                    runs[(t["id"], ev["id"])] = res
                    if res.get("ok"):
                        newc = res.get("new_count") or 0
                        suffix = f", {newc} new" if newc else ""
                        status.write(f"  → {len(res['df'])} players{suffix}")
                    else:
                        status.write(f"  ✗ {res.get('error')}")
                st.session_state._finder_runs = runs
                st.session_state._finder_selected = [
                    {"kind": "event", "tid": t["id"], "key": (t["id"], ev["id"]),
                     "key_slug": f"{t['id']}_{ev['id']}",
                     "name": t["name"], "label": ev.get("label"),
                     "event": ev, "division": ""}
                    for t, ev in selected
                ]
                status.update(label="Done", state="complete", expanded=False)
                st.rerun()

    runs = st.session_state.get("_finder_runs") or {}
    picked = st.session_state.get("_finder_selected") or []
    if picked and runs:
        st.divider()
        st.subheader("Selected event results")
        labels = [f"{p.get('name')} — {p.get('label')}" for p in picked]
        tabs = st.tabs(labels)
        for tab, item in zip(tabs, picked):
            with tab:
                res = runs.get(item.get("key"))
                if not res or not res.get("ok"):
                    st.error((res or {}).get("error", "Run failed"))
                    continue
                if res.get("rate_limited"):
                    st.warning("UTR was rate-limited during this run; cached ratings were used where available.")
                render_event_result(res, item)


@st.fragment(run_every=1)
def render_utr_rate_limit_banner():
    """Live countdown while UTR API queries are halted (auto-updates every second)."""
    if not UTRScraper.is_rate_limited():
        return
    remaining = UTRScraper.rate_limit_remaining()
    st.warning(
        "**UTR API rate limit active.** All UTR API queries are halted for "
        f"**{format_duration(remaining)}**. Cached UTR data is still shown where available.",
        icon="⏸️",
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
if "_finder_results" not in st.session_state:
    st.session_state._finder_results = []
if "_finder_details" not in st.session_state:
    st.session_state._finder_details = {}
if "_finder_runs" not in st.session_state:
    st.session_state._finder_runs = {}
if "_finder_selected" not in st.session_state:
    st.session_state._finder_selected = []

# ── Auto-check login status on startup ──
if not st.session_state._login_checked:
    st.session_state._usta_login = check_usta_login()
    st.session_state._utr_login = check_utr_login()
    st.session_state._login_checked = True

# ── Sidebar ─────────────────────────────────────────────────────────
col_title, col_toggle = st.columns([6, 1])
with col_title:
    st.markdown("### TennisLink")
if col_toggle.button("☰", help="Toggle sidebar"):
    st.session_state.sidebar_open = not st.session_state.sidebar_open
    st.rerun()

render_utr_rate_limit_banner()

# ── Sidebar: global login + mapping DB only ─────────────────────────
if st.session_state.sidebar_open:
    with st.sidebar:
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
        try:
            st.caption(f"Mapping DB: {count_mappings()} player(s)")
        except Exception:
            pass

# ── Pipeline execution (Tournament Players) ─────────────────────────
if st.session_state._run_pending:
    st.session_state._run_pending = False
    s = st.session_state._saved_inputs or {}
    tournament = s.get("tournament", "")
    division = s.get("division", "")
    no_login = s.get("no_login", False)
    no_profiles = s.get("no_profiles", False)
    no_cross_ref = s.get("no_cross_ref", False)
    visible = s.get("visible", False)

    if not tournament:
        st.error("Enter a tournament GUID or URL.")
        st.stop()

    status = st.status("Starting...", expanded=True)
    if not check_usta_login():
        st.warning("⚠️ USTA not logged in — tournament draw may be unavailable.")
    if not check_utr_login():
        st.info("ℹ️ UTR not logged in — precise UTR scrape skipped; cached ratings still used.")

    try:
        res = run_division(
            tournament, division,
            run_profiles=not no_profiles,
            no_login=no_login,
            no_cross_ref=no_cross_ref,
            force_refresh_ids=st.session_state.force_refresh,
            headless=not visible,
            progress=status.write,
        )
        if not res.get("ok"):
            status.update(label="Error", state="error")
            st.error(res.get("error", "Run failed"))
        else:
            st.session_state.force_refresh = set()
            status.update(label="Complete!", state="complete", expanded=False)
            st.session_state.last_run = {
                "df": res["df"],
                "fname": res["fname"],
                "out_path": res["out_path"],
                "usta_ids": res["df"]["USTA ID"].tolist(),
                "ranking_label": res["ranking_label"],
            }
            st.session_state._active_tab = "🎾 Tournament Players"
            st.rerun()
    except Exception as e:
        status.update(label="Error", state="error")
        st.exception(e)

# ── Main tabs ───────────────────────────────────────────────────────
TAB_GUIDE = "📖 User Guide"
TAB_TOURNAMENT = "🎾 Tournament Players"
TAB_FINDER = "🔎 Tournament Finder"
TAB_BOOTSTRAP = "🧩 Bootstrap Pairs"
_TABS = [TAB_GUIDE, TAB_TOURNAMENT, TAB_FINDER, TAB_BOOTSTRAP]

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

# ── Display ────────────────────────────────────────────────────────
if st.session_state._active_tab == TAB_GUIDE:
    render_user_guide()
elif st.session_state._active_tab == TAB_TOURNAMENT:
    render_players_form()
elif st.session_state._active_tab == TAB_FINDER:
    render_finder_tab()
elif st.session_state._active_tab == TAB_BOOTSTRAP:
    render_bootstrap_tab()

if (st.session_state._active_tab == TAB_TOURNAMENT
        and st.session_state.last_run is not None):
    _run = st.session_state.last_run

    def _players_refresh():
        st.session_state.force_refresh = set(_run["usta_ids"])
        st.session_state._run_pending = True

    def _players_correct(usta_id, new_utr):
        save_mapping(usta_id, new_utr, match_method="manual_correct", confidence=1.0)
        try:
            UTRScraper().get_player_profile(new_utr)
        except Exception:
            pass
        st.session_state.force_refresh = set()
        st.session_state._run_pending = True
        st.rerun()

    render_results_panel(
        _run["df"], _run["fname"], _run.get("ranking_label", "Rank Pts"),
        "players", out_path=_run.get("out_path"),
        on_refresh=_players_refresh, on_correct=_players_correct,
    )
elif st.session_state._active_tab == TAB_TOURNAMENT:
    st.info("No tournament loaded yet. Enter a tournament above and click **Run**.")
