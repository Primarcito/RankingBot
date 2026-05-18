import sqlite3
from datetime import datetime

DB_PATH = "scouts.db"

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
                kill_persona INTEGER DEFAULT 0,
                limpieza_aspecto INTEGER DEFAULT 0,
                scouteo INTEGER DEFAULT 0,
                mapeo INTEGER DEFAULT 0,
                prio_lider INTEGER DEFAULT 0
            )
        """)
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
        c.execute("PRAGMA table_info(evidence_messages)")
        cols = [row[1] for row in c.fetchall()]
        if "status" not in cols:
            c.execute("ALTER TABLE evidence_messages ADD COLUMN status TEXT DEFAULT 'approved'")
        if "review_message_id" not in cols:
            c.execute("ALTER TABLE evidence_messages ADD COLUMN review_message_id TEXT")
        # Valores por defecto de configuración
        defaults = [
            ("kill_scout", 10),
            ("kill_persona", 3),
            ("limpieza_aspecto", 8),
            ("scouteo", 6),
            ("mapeo", 5),
            ("prio_lider", 10),
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
        row = conn.execute("SELECT * FROM scouts WHERE user_id=?", (user_id,)).fetchone()
    return row  # (user_id, username, kill_scout, kill_persona, limpieza_aspecto, scouteo, mapeo, prio_lider)

def get_all_scouts():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM scouts").fetchall()
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

def add_evidence_activity(message_id: str, user_id: str, username: str, actividad: str):
    ensure_scout(user_id, username)
    puntos_unit = get_puntos(actividad)
    fecha = datetime.utcnow().isoformat()
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO evidence_messages (message_id, user_id, actividad, puntos, fecha) VALUES (?,?,?,?,?)",
                (message_id, user_id, actividad, puntos_unit, fecha)
            )
        except sqlite3.IntegrityError:
            return 0
        conn.execute(
            f"UPDATE scouts SET {actividad} = {actividad} + 1 WHERE user_id=?",
            (user_id,)
        )
        conn.execute(
            "INSERT INTO logs (user_id, username, actividad, cantidad, puntos, fecha, accion) VALUES (?,?,?,?,?,?,?)",
            (user_id, username, actividad, 1, puntos_unit, fecha, "evidencia")
        )
        conn.commit()
    return puntos_unit

def create_evidence_review(message_id: str, user_id: str, username: str, actividad: str):
    ensure_scout(user_id, username)
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
        conn.commit()
    return puntos_unit

def set_evidence_review_message(message_id: str, review_message_id: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE evidence_messages SET review_message_id=? WHERE message_id=?",
            (review_message_id, message_id)
        )
        conn.commit()

def approve_evidence(message_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id, actividad, puntos, status FROM evidence_messages WHERE message_id=?",
            (message_id,)
        ).fetchone()
        if not row or row[3] != "pending":
            return None
        user_id, actividad, puntos, _ = row
        scout = conn.execute("SELECT username FROM scouts WHERE user_id=?", (user_id,)).fetchone()
        username = scout[0] if scout else user_id
        conn.execute(f"UPDATE scouts SET {actividad} = {actividad} + 1 WHERE user_id=?", (user_id,))
        conn.execute(
            "UPDATE evidence_messages SET status='approved' WHERE message_id=?",
            (message_id,)
        )
        conn.execute(
            "INSERT INTO logs (user_id, username, actividad, cantidad, puntos, fecha, accion) VALUES (?,?,?,?,?,?,?)",
            (user_id, username, actividad, 1, puntos, datetime.utcnow().isoformat(), "evidencia_aprobada")
        )
        conn.commit()
    return user_id, actividad, puntos

def reject_evidence(message_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM evidence_messages WHERE message_id=?",
            (message_id,)
        ).fetchone()
        if not row or row[0] != "pending":
            return False
        conn.execute(
            "UPDATE evidence_messages SET status='rejected' WHERE message_id=?",
            (message_id,)
        )
        conn.commit()
    return True

def subtract_activity(user_id: str, username: str, actividad: str, cantidad: int):
    ensure_scout(user_id, username)
    row = get_scout(user_id)
    cols = ["kill_scout","kill_persona","limpieza_aspecto","scouteo","mapeo","prio_lider"]
    current = row[cols.index(actividad) + 2]
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

COLS = ["kill_scout","kill_persona","limpieza_aspecto","scouteo","mapeo","prio_lider"]

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
