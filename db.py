import sqlite3
import datetime
from config import DB_PATH

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Create usta_cache table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usta_cache (
                usta_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                city TEXT,
                state TEXT,
                wtn_singles REAL,
                wtn_doubles REAL,
                usta_ranking INTEGER,
                last_updated TEXT NOT NULL
            )
        """)
        
        # Create utr_cache table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS utr_cache (
                utr_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                city TEXT,
                state TEXT,
                utr_singles REAL,
                utr_doubles REAL,
                last_updated TEXT NOT NULL
            )
        """)
        
        # Create player_mappings table
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

def save_usta_cache(usta_id, name, city=None, state=None, wtn_singles=None, wtn_doubles=None, usta_ranking=None):
    now = datetime.datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO usta_cache (usta_id, name, city, state, wtn_singles, wtn_doubles, usta_ranking, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(usta_id) DO UPDATE SET
                name=excluded.name,
                city=excluded.city,
                state=excluded.state,
                wtn_singles=excluded.wtn_singles,
                wtn_doubles=excluded.wtn_doubles,
                usta_ranking=excluded.usta_ranking,
                last_updated=excluded.last_updated
        """, (usta_id, name, city, state, wtn_singles, wtn_doubles, usta_ranking, now))
        conn.commit()

def get_usta_cache(usta_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM usta_cache WHERE usta_id = ?", (usta_id,)).fetchone()
        return dict(row) if row else None

def save_utr_cache(utr_id, name, city=None, state=None, utr_singles=None, utr_doubles=None):
    now = datetime.datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO utr_cache (utr_id, name, city, state, utr_singles, utr_doubles, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(utr_id) DO UPDATE SET
                name=excluded.name,
                city=excluded.city,
                state=excluded.state,
                utr_singles=excluded.utr_singles,
                utr_doubles=excluded.utr_doubles,
                last_updated=excluded.last_updated
        """, (utr_id, name, city, state, utr_singles, utr_doubles, now))
        conn.commit()

def get_utr_cache(utr_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM utr_cache WHERE utr_id = ?", (utr_id,)).fetchone()
        return dict(row) if row else None

def save_mapping(usta_id, utr_id, match_method="manual", confidence=1.0):
    now = datetime.datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO player_mappings (usta_id, utr_id, match_method, confidence, last_updated)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(usta_id) DO UPDATE SET
                utr_id=excluded.utr_id,
                match_method=excluded.match_method,
                confidence=excluded.confidence,
                last_updated=excluded.last_updated
        """, (usta_id, utr_id, match_method, confidence, now))
        conn.commit()

def get_mapping(usta_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM player_mappings WHERE usta_id = ?", (usta_id,)).fetchone()
        return dict(row) if row else None

# Auto-initialize database on import
init_db()
