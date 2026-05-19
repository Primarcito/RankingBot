import os
import sqlite3
from datetime import datetime

DATA_DIR = os.getenv("DATA_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or "."
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "scouts.db")

def get_conn():
    return sqlite3.connect(DB_PATH)

def init_db():
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS scouts (
                user_id TEXT PRIMARY KEY,
                username TEXT,
                kill_scout INTEGER DEFAULT 0,
                kill_pelea INTEGER DEFAULT 0,
                limpieza_aspecto INTEGER DEFAULT 0,
                scouteo INTEGER DEFAULT 0,
                mapeo INTEGER DEFAULT 0
            )
        """)
        c.execute("PRAGMA table_info(scouts)")
        scout_cols = [row[1] for row in c.fetchall()]
        if "kill_pelea" not in scout_cols:
            c.execute("ALTER TABLE scouts ADD COLUMN kill_pelea INTEGER DEFAULT 0")
        if "kill_persona" in scout_cols:
            c.execute("UPDATE scouts SET kill_pelea = kill_pelea + kill_persona")
        c.execute("""
            CREATE TABLE IF NOT EXISTS config (
                actividad TEXT PRIMARY KEY,
                puntos INTEGER
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                username TEXT,
                actividad TEXT,
                cantidad INTEGER,
                puntos INTEGER,
                fecha TEXT,
                accion TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS evidence_messages (
                message_id TEXT PRIMARY KEY,
                user_id TEXT,
                actividad TEXT,
                puntos INTEGER,
                fecha TEXT,
                status TEXT DEFAULT 'approved',
                review_message_id TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS evidence_participants (
                message_id TEXT,
                user_id TEXT,
                username TEXT,
                PRIMARY KEY (message_id, user_id)
            )
        """)
        c.execute("PRAGMA table_info(evidence_messages)")
        cols = [row[1] for row in c.fetchall()]
        if "status" not in cols:
            c.execute("ALTER TABLE evidence_messages ADD COLUMN status TEXT DEFAULT 'approved'")
        if "review_message_id" not in cols:
            c.execute("ALTER TABLE evidence_messages ADD COLUMN review_message_id TEXT")
        # Valores por defecto de configuración
        defaults = [
            ("kill_scout", 2),
            ("kill_pelea", 3),
            ("limpieza_aspecto", 1),
            ("scouteo", 2),
            ("mapeo", 1),
        ]
        c.executemany("INSERT OR IGNORE INTO config (actividad, puntos) VALUES (?, ?)", defaults)
        conn.commit()

# ── Scouts ──────────────────────────────────────────────────────────────────

def ensure_scout(user_id: str, username: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO scouts (user_id, username) VALUES (?, ?)",
            (user_id, username)
        )
        conn.execute("UPDATE scouts SET username=? WHERE user_id=?", (username, user_id))
        conn.commit()

def get_scout(user_id: str):
    with get_conn() as conn:
        row = conn.execute(scout_select_sql("WHERE user_id=?"), (user_id,)).fetchone()
    return row

def get_all_scouts():
    with get_conn() as conn:
        rows = conn.execute(scout_select_sql()).fetchall()
    return rows

def add_activity(user_id: str, username: str, actividad: str, cantidad: int):
    ensure_scout(user_id, username)
    puntos_unit = get_puntos(actividad)
    total_puntos = puntos_unit * cantidad
    with get_conn() as conn:
        conn.execute(
            f"UPDATE scouts SET {actividad} = {actividad} + ? WHERE user_id=?",
            (cantidad, user_id)
        )
        conn.execute(
            "INSERT INTO logs (user_id, username, actividad, cantidad, puntos, fecha, accion) VALUES (?,?,?,?,?,?,?)",
            (user_id, username, actividad, cantidad, total_puntos, datetime.utcnow().isoformat(), "suma")
        )
        conn.commit()
    return total_puntos

def create_evidence_review(message_id: str, user_id: str, username: str, actividad: str, participants=None):
    ensure_scout(user_id, username)
    participants = participants or [(user_id, username)]
    puntos_unit = get_puntos(actividad)
    fecha = datetime.utcnow().isoformat()
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO evidence_messages (message_id, user_id, actividad, puntos, fecha, status) VALUES (?,?,?,?,?,?)",
                (message_id, user_id, actividad, puntos_unit, fecha, "pending")
            )
        except sqlite3.IntegrityError:
            return 0
        for participant_id, participant_name in participants:
            conn.execute(
                "INSERT OR IGNORE INTO scouts (user_id, username) VALUES (?, ?)",
                (str(participant_id), participant_name)
            )
            conn.execute("UPDATE scouts SET username=? WHERE user_id=?", (participant_name, str(participant_id)))
            conn.execute(
                "INSERT OR IGNORE INTO evidence_participants (message_id, user_id, username) VALUES (?,?,?)",
                (message_id, str(participant_id), participant_name)
            )
        conn.commit()
    return puntos_unit

def set_evidence_review_message(message_id: str, review_message_id: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE evidence_messages SET review_message_id=? WHERE message_id=?",
            (review_message_id, message_id)
        )
        conn.commit()

def add_evidence_participants(message_id: str, participants):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM evidence_messages WHERE message_id=?",
            (message_id,)
        ).fetchone()
        if not row or row[0] != "pending":
            return False
        for participant_id, participant_name in participants:
            conn.execute(
                "INSERT OR IGNORE INTO scouts (user_id, username) VALUES (?, ?)",
                (str(participant_id), participant_name)
            )
            conn.execute("UPDATE scouts SET username=? WHERE user_id=?", (participant_name, str(participant_id)))
            conn.execute(
                "INSERT OR IGNORE INTO evidence_participants (message_id, user_id, username) VALUES (?,?,?)",
                (message_id, str(participant_id), participant_name)
            )
        conn.commit()
    return True

def get_evidence_participants(message_id: str):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id, username FROM evidence_participants WHERE message_id=?",
            (message_id,)
        ).fetchall()
    return rows

def get_pending_evidence_message_ids():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT message_id FROM evidence_messages WHERE status='pending'"
        ).fetchall()
    return [row[0] for row in rows]

def get_pending_count() -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM evidence_messages WHERE status='pending'"
        ).fetchone()
    return row[0] if row else 0

def get_today_evidence_count() -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM evidence_messages WHERE DATE(fecha) = DATE('now')"
        ).fetchone()
    return row[0] if row else 0

def approve_evidence(message_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id, actividad, puntos, status, review_message_id FROM evidence_messages WHERE message_id=?",
            (message_id,)
        ).fetchone()
        if not row or row[3] != "pending":
            return None
        user_id, actividad, puntos, _, review_message_id = row
        participants = conn.execute(
            "SELECT user_id, username FROM evidence_participants WHERE message_id=?",
            (message_id,)
        ).fetchall()
        if not participants:
            scout = conn.execute("SELECT username FROM scouts WHERE user_id=?", (user_id,)).fetchone()
            participants = [(user_id, scout[0] if scout else user_id)]

        for participant_id, username in participants:
            conn.execute(
                "INSERT OR IGNORE INTO scouts (user_id, username) VALUES (?, ?)",
                (str(participant_id), username)
            )
            conn.execute("UPDATE scouts SET username=? WHERE user_id=?", (username, str(participant_id)))
            conn.execute(f"UPDATE scouts SET {actividad} = {actividad} + 1 WHERE user_id=?", (participant_id,))
            conn.execute(
                "INSERT INTO logs (user_id, username, actividad, cantidad, puntos, fecha, accion) VALUES (?,?,?,?,?,?,?)",
                (participant_id, username, actividad, 1, puntos, datetime.utcnow().isoformat(), "evidencia_aprobada")
            )
        conn.execute(
            "UPDATE evidence_messages SET status='approved' WHERE message_id=?",
            (message_id,)
        )
        conn.commit()
    return user_id, actividad, puntos, review_message_id

def find_scout_by_name(name: str):
    target = normalize_name(name)
    with get_conn() as conn:
        rows = conn.execute("SELECT user_id, username FROM scouts").fetchall()
    for user_id, username in rows:
        if normalize_name(username) == target:
            return user_id, username
    return None

def reject_evidence(message_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status, review_message_id FROM evidence_messages WHERE message_id=?",
            (message_id,)
        ).fetchone()
        if not row or row[0] != "pending":
            return False
        conn.execute(
            "UPDATE evidence_messages SET status='rejected' WHERE message_id=?",
            (message_id,)
        )
        conn.commit()
    return row[1]

def subtract_activity(user_id: str, username: str, actividad: str, cantidad: int):
    ensure_scout(user_id, username)
    row = get_scout(user_id)
    current = row[COLS.index(actividad) + 2]
    real_sub = min(cantidad, current)  # no negativos
    puntos_unit = get_puntos(actividad)
    total_puntos = puntos_unit * real_sub
    with get_conn() as conn:
        conn.execute(
            f"UPDATE scouts SET {actividad} = MAX(0, {actividad} - ?) WHERE user_id=?",
            (cantidad, user_id)
        )
        conn.execute(
            "INSERT INTO logs (user_id, username, actividad, cantidad, puntos, fecha, accion) VALUES (?,?,?,?,?,?,?)",
            (user_id, username, actividad, real_sub, total_puntos, datetime.utcnow().isoformat(), "resta")
        )
        conn.commit()
    return total_puntos

def reset_all():
    with get_conn() as conn:
        conn.execute("DELETE FROM scouts")
        conn.commit()

# ── Config ───────────────────────────────────────────────────────────────────

def get_puntos(actividad: str) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT puntos FROM config WHERE actividad=?", (actividad,)).fetchone()
    return row[0] if row else 0

def set_puntos(actividad: str, puntos: int):
    with get_conn() as conn:
        conn.execute("UPDATE config SET puntos=? WHERE actividad=?", (puntos, actividad))
        conn.commit()

def get_all_config():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM config").fetchall()
    return rows

# ── Helpers ───────────────────────────────────────────────────────────────────

COLS = ["kill_scout","kill_pelea","limpieza_aspecto","scouteo","mapeo"]

def scout_select_sql(where_clause: str = ""):
    return "SELECT user_id, username, kill_scout, kill_pelea, limpieza_aspecto, scouteo, mapeo FROM scouts " + where_clause

def calc_puntos_totales(row) -> int:
    config = {a: p for a, p in get_all_config()}
    total = 0
    for i, col in enumerate(COLS):
        total += row[i + 2] * config.get(col, 0)
    return total

def get_nivel(puntos: int) -> tuple[str, str]:
    if puntos >= 120:
        return "S", "Máxima prioridad"
    elif puntos >= 80:
        return "A", "Alta prioridad"
    elif puntos >= 50:
        return "B", "Prioridad media"
    elif puntos >= 20:
        return "C", "Prioridad básica"
    else:
        return "Inactivo", "Sin prioridad"

def normalize_name(name: str) -> str:
    return "".join(ch.lower() for ch in name if ch.isalnum())
