import sqlite3
import json
import datetime
from scripts.config import DB_PATH

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _column_exists(cursor, table, column):
    cols = [r[1] for r in cursor.execute(f"PRAGMA table_info({table})")]
    return column in cols

def _table_exists(cursor, name):
    row = cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None

def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()

        # Migration: rename old table names to new names
        old_new = [
            ("usta_cache", "usta_player_profiles"),
            ("utr_cache", "utr_player_profiles"),
            ("usta_rankings", "usta_player_rankings"),
        ]
        for old, new in old_new:
            if _table_exists(cursor, old) and not _table_exists(cursor, new):
                cursor.execute(f"ALTER TABLE {old} RENAME TO {new}")

        # Create usta_player_profiles (if not exists from rename)
        if not _table_exists(cursor, "usta_player_profiles"):
            cursor.execute("""
                CREATE TABLE usta_player_profiles (
                    usta_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    city TEXT,
                    state TEXT,
                    wtn_singles REAL,
                    wtn_doubles REAL,
                    usta_ranking INTEGER,
                    section TEXT,
                    district TEXT,
                    gender TEXT,
                    age_category TEXT,
                    ball_color TEXT,
                    competition_level TEXT,
                    itf_tennis_id TEXT,
                    nationality TEXT,
                    wtn_singles_confidence INTEGER,
                    wtn_singles_date TEXT,
                    wtn_doubles_confidence INTEGER,
                    wtn_doubles_date TEXT,
                    ranking_json TEXT,
                    last_updated TEXT NOT NULL
                )
            """)
        else:
            # Add ranking_json column if missing (migration)
            if not _column_exists(cursor, "usta_player_profiles", "ranking_json"):
                cursor.execute("ALTER TABLE usta_player_profiles ADD COLUMN ranking_json TEXT")

        # Create usta_player_rankings (legacy, populated from ranking_json)
        if not _table_exists(cursor, "usta_player_rankings"):
            cursor.execute("""
                CREATE TABLE usta_player_rankings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    usta_id TEXT NOT NULL,
                    display_label TEXT,
                    age_restriction TEXT,
                    list_type TEXT,
                    match_format TEXT,
                    rank_list_gender TEXT,
                    rank_national INTEGER,
                    rank_section INTEGER,
                    rank_district INTEGER,
                    points INTEGER,
                    points_singles INTEGER,
                    points_doubles INTEGER,
                    points_bonus INTEGER,
                    wins INTEGER,
                    losses INTEGER,
                    trend_direction TEXT,
                    publish_date TEXT,
                    last_updated TEXT NOT NULL,
                    FOREIGN KEY (usta_id) REFERENCES usta_player_profiles(usta_id)
                )
            """)

        # Create utr_player_profiles
        if not _table_exists(cursor, "utr_player_profiles"):
            cursor.execute("""
                CREATE TABLE utr_player_profiles (
                    utr_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    city TEXT,
                    state TEXT,
                    utr_singles REAL,
                    utr_doubles REAL,
                    last_updated TEXT NOT NULL
                )
            """)

        # Create history tables
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usta_player_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usta_id TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                profile_json TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS utr_player_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                utr_id TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                profile_json TEXT NOT NULL
            )
        """)

        # player_mappings (unchanged)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS player_mappings (
                usta_id TEXT PRIMARY KEY,
                utr_id TEXT,
                match_method TEXT,
                confidence REAL,
                last_updated TEXT NOT NULL
            )
        """)

        conn.commit()


# ── USTA player profiles ──

def save_usta_player_profile(usta_id, name, city=None, state=None, section=None, district=None,
                             gender=None, age_category=None, ball_color=None, competition_level=None,
                             itf_tennis_id=None, nationality=None,
                             wtn_singles=None, wtn_singles_confidence=None, wtn_singles_date=None,
                             wtn_doubles=None, wtn_doubles_confidence=None, wtn_doubles_date=None,
                             rankings=None):
    now = datetime.datetime.now().isoformat()
    ranking_json = json.dumps(rankings) if rankings else None
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO usta_player_profiles (
                usta_id, name, city, state, section, district,
                gender, age_category, ball_color, competition_level,
                itf_tennis_id, nationality,
                wtn_singles, wtn_singles_confidence, wtn_singles_date,
                wtn_doubles, wtn_doubles_confidence, wtn_doubles_date,
                ranking_json, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(usta_id) DO UPDATE SET
                name=excluded.name, city=excluded.city, state=excluded.state,
                section=excluded.section, district=excluded.district,
                gender=excluded.gender, age_category=excluded.age_category,
                ball_color=excluded.ball_color, competition_level=excluded.competition_level,
                itf_tennis_id=excluded.itf_tennis_id, nationality=excluded.nationality,
                wtn_singles=excluded.wtn_singles,
                wtn_singles_confidence=excluded.wtn_singles_confidence,
                wtn_singles_date=excluded.wtn_singles_date,
                wtn_doubles=excluded.wtn_doubles,
                wtn_doubles_confidence=excluded.wtn_doubles_confidence,
                wtn_doubles_date=excluded.wtn_doubles_date,
                ranking_json=excluded.ranking_json,
                last_updated=excluded.last_updated
        """, (usta_id, name, city, state, section, district,
              gender, age_category, ball_color, competition_level,
              itf_tennis_id, nationality,
              wtn_singles, wtn_singles_confidence, wtn_singles_date,
              wtn_doubles, wtn_doubles_confidence, wtn_doubles_date,
              ranking_json, now))

        # Also populate legacy player_rankings table
        conn.execute("DELETE FROM usta_player_rankings WHERE usta_id = ?", (usta_id,))
        if rankings:
            for r in rankings:
                if not r.get("display_label"):
                    continue
                conn.execute("""
                    INSERT INTO usta_player_rankings (
                        usta_id, display_label, age_restriction, list_type, match_format,
                        rank_list_gender, rank_national, rank_section, rank_district,
                        points, points_singles, points_doubles, points_bonus,
                        wins, losses, trend_direction, publish_date, last_updated
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    usta_id,
                    r.get("display_label"),
                    r.get("age_restriction"),
                    r.get("list_type"),
                    r.get("match_format"),
                    r.get("rank_list_gender"),
                    r.get("rank_national"),
                    r.get("rank_section"),
                    r.get("rank_district"),
                    r.get("points"),
                    r.get("points_singles"),
                    r.get("points_doubles"),
                    r.get("points_bonus"),
                    r.get("wins"),
                    r.get("losses"),
                    r.get("trend_direction"),
                    r.get("publish_date"),
                    now
                ))
        conn.commit()


def get_usta_player_profile(usta_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM usta_player_profiles WHERE usta_id = ?", (usta_id,)).fetchone()
        if row:
            d = dict(row)
            if d.get("ranking_json"):
                d["rankings"] = json.loads(d["ranking_json"])
            return d
        return None


def get_usta_rankings(usta_id):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM usta_player_rankings WHERE usta_id = ? ORDER BY age_restriction, list_type",
            (usta_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def save_usta_player_history(usta_id, profile_data: dict):
    now = datetime.datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO usta_player_history (usta_id, snapshot_date, profile_json) VALUES (?, ?, ?)",
            (usta_id, now, json.dumps(profile_data, default=str))
        )
        conn.commit()


# ── UTR player profiles ──

def save_utr_player_profile(utr_id, name, city=None, state=None, utr_singles=None, utr_doubles=None):
    now = datetime.datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO utr_player_profiles (utr_id, name, city, state, utr_singles, utr_doubles, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(utr_id) DO UPDATE SET
                name=excluded.name, city=excluded.city, state=excluded.state,
                utr_singles=excluded.utr_singles, utr_doubles=excluded.utr_doubles,
                last_updated=excluded.last_updated
        """, (utr_id, name, city, state, utr_singles, utr_doubles, now))
        conn.commit()


def get_utr_player_profile(utr_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM utr_player_profiles WHERE utr_id = ?", (utr_id,)).fetchone()
        return dict(row) if row else None


def save_utr_player_history(utr_id, profile_data: dict):
    now = datetime.datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO utr_player_history (utr_id, snapshot_date, profile_json) VALUES (?, ?, ?)",
            (utr_id, now, json.dumps(profile_data, default=str))
        )
        conn.commit()


# ── Legacy aliases for backward compatibility ──
save_usta_cache = save_usta_player_profile
get_usta_cache = get_usta_player_profile
save_utr_cache = save_utr_player_profile
get_utr_cache = get_utr_player_profile
save_rankings = lambda usta_id, rankings: None  # no-op, rankings stored in profile JSON


# ── Mappings ──

def save_mapping(usta_id, utr_id, match_method="manual", confidence=1.0):
    now = datetime.datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO player_mappings (usta_id, utr_id, match_method, confidence, last_updated)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(usta_id) DO UPDATE SET
                utr_id=excluded.utr_id, match_method=excluded.match_method,
                confidence=excluded.confidence, last_updated=excluded.last_updated
        """, (usta_id, utr_id, match_method, confidence, now))
        conn.commit()


def get_mapping(usta_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM player_mappings WHERE usta_id = ?", (usta_id,)).fetchone()
        return dict(row) if row else None


def delete_mapping(usta_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM player_mappings WHERE usta_id = ?", (usta_id,))
        conn.commit()


def get_all_cache(table_name):
    with get_connection() as conn:
        rows = conn.execute(f"SELECT * FROM {table_name}").fetchall()
        return [dict(r) for r in rows]


def count_mappings() -> int:
    with get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM player_mappings").fetchone()[0]


def get_all_mappings() -> list:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM player_mappings").fetchall()
        return [dict(r) for r in rows]


def reset_all():
    """Delete every row from every table. Intended for scratch/bootstrap DBs only."""
    tables = (
        "player_mappings",
        "usta_player_rankings",
        "usta_player_history",
        "usta_player_profiles",
        "utr_player_history",
        "utr_player_profiles",
    )
    with get_connection() as conn:
        for t in tables:
            conn.execute(f"DELETE FROM {t}")
        conn.commit()



init_db()
