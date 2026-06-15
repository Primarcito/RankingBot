import os
import sqlite3
import unicodedata
from datetime import datetime

DATA_DIR = os.getenv("DATA_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or "."
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "scouts.db")
DEFAULT_ACTIVITY_POINTS = {
    "kill_scout": 2,
    "kill_pelea": 3,
    "limpieza_aspecto": 1,
    "scouteo": 2,
    "mapeo": 1,
}
ACTIVITY_COLUMNS = tuple(DEFAULT_ACTIVITY_POINTS.keys())
CONFIG_REPAIR_MARKER = "__repair_points_config_2026_05_25"
PRIORITY_LEVELS = (
    {"level": "S", "min": 120, "max": None, "benefit": "Maxima prioridad"},
    {"level": "A", "min": 80, "max": 119, "benefit": "Alta prioridad"},
    {"level": "B", "min": 50, "max": 79, "benefit": "Prioridad media"},
    {"level": "C", "min": 20, "max": 49, "benefit": "Prioridad basica"},
    {"level": "Inactivo", "min": 0, "max": 19, "benefit": "Sin prioridad"},
)

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
                review_message_id TEXT,
                thread_id TEXT,
                target_snapshot_id INTEGER
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS evidence_participants (
                message_id TEXT,
                user_id TEXT,
                username TEXT,
                cantidad INTEGER DEFAULT 1,
                PRIMARY KEY (message_id, user_id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS scout_aliases (
                normalized_alias TEXT PRIMARY KEY,
                alias TEXT,
                user_id TEXT,
                username TEXT,
                created_at TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS ranking_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                period_start TEXT,
                period_end TEXT,
                reason TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS ranking_snapshot_rows (
                snapshot_id INTEGER,
                user_id TEXT,
                username TEXT,
                kill_scout INTEGER,
                kill_pelea INTEGER,
                limpieza_aspecto INTEGER,
                scouteo INTEGER,
                mapeo INTEGER,
                total_puntos INTEGER,
                nivel TEXT,
                beneficio TEXT,
                PRIMARY KEY (snapshot_id, user_id)
            )
        """)
        c.execute("PRAGMA table_info(evidence_messages)")
        cols = [row[1] for row in c.fetchall()]
        if "status" not in cols:
            c.execute("ALTER TABLE evidence_messages ADD COLUMN status TEXT DEFAULT 'approved'")
        if "review_message_id" not in cols:
            c.execute("ALTER TABLE evidence_messages ADD COLUMN review_message_id TEXT")
        if "thread_id" not in cols:
            c.execute("ALTER TABLE evidence_messages ADD COLUMN thread_id TEXT")
        if "target_snapshot_id" not in cols:
            c.execute("ALTER TABLE evidence_messages ADD COLUMN target_snapshot_id INTEGER")
        c.execute("PRAGMA table_info(evidence_participants)")
        participant_cols = [row[1] for row in c.fetchall()]
        if "cantidad" not in participant_cols:
            c.execute("ALTER TABLE evidence_participants ADD COLUMN cantidad INTEGER DEFAULT 1")
        # Valores por defecto de configuración
        defaults = list(DEFAULT_ACTIVITY_POINTS.items())
        c.executemany("INSERT OR IGNORE INTO config (actividad, puntos) VALUES (?, ?)", defaults)
        repair_points_config_if_reset(c)
        conn.commit()

def repair_points_config_if_reset(cursor):
    marker = cursor.execute(
        "SELECT puntos FROM config WHERE actividad=?",
        (CONFIG_REPAIR_MARKER,)
    ).fetchone()
    if marker:
        return

    activity_keys = tuple(DEFAULT_ACTIVITY_POINTS.keys())
    placeholders = ",".join("?" for _ in activity_keys)
    rows = cursor.execute(
        f"SELECT actividad, puntos FROM config WHERE actividad IN ({placeholders})",
        activity_keys,
    ).fetchall()
    current_points = {activity: points for activity, points in rows}
    was_reset_to_one = (
        all(activity in current_points for activity in DEFAULT_ACTIVITY_POINTS)
        and all(current_points[activity] == 1 for activity in DEFAULT_ACTIVITY_POINTS)
    )

    if was_reset_to_one:
        cursor.executemany(
            "UPDATE config SET puntos=? WHERE actividad=?",
            [(points, activity) for activity, points in DEFAULT_ACTIVITY_POINTS.items()]
        )

    cursor.execute(
        "INSERT OR IGNORE INTO config (actividad, puntos) VALUES (?, ?)",
        (CONFIG_REPAIR_MARKER, 1)
    )

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

def create_evidence_review(
    message_id: str,
    user_id: str,
    username: str,
    actividad: str,
    participants=None,
    target_snapshot_id=None,
):
    target_snapshot_id = int(target_snapshot_id) if target_snapshot_id else None
    if not target_snapshot_id:
        ensure_scout(user_id, username)
    participants = participants or [(user_id, username)]
    puntos_unit = get_puntos(actividad)
    fecha = datetime.utcnow().isoformat()
    with get_conn() as conn:
        try:
            conn.execute(
                """
                INSERT INTO evidence_messages (
                    message_id, user_id, actividad, puntos, fecha, status, target_snapshot_id
                )
                VALUES (?,?,?,?,?,?,?)
                """,
                (message_id, user_id, actividad, puntos_unit, fecha, "pending", target_snapshot_id)
            )
        except sqlite3.IntegrityError:
            return 0
        for participant in participants:
            participant_id, participant_name, cantidad = normalize_participant_entry(participant)
            if not target_snapshot_id:
                conn.execute(
                    "INSERT OR IGNORE INTO scouts (user_id, username) VALUES (?, ?)",
                    (str(participant_id), participant_name)
                )
                conn.execute("UPDATE scouts SET username=? WHERE user_id=?", (participant_name, str(participant_id)))
            conn.execute(
                """
                INSERT OR IGNORE INTO evidence_participants (message_id, user_id, username, cantidad)
                VALUES (?,?,?,?)
                """,
                (message_id, str(participant_id), participant_name, cantidad)
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

def set_evidence_thread(message_id: str, thread_id: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE evidence_messages SET thread_id=? WHERE message_id=?",
            (str(thread_id), str(message_id))
        )
        conn.commit()

def get_evidence_by_thread(thread_id: str):
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT message_id, user_id, actividad, status, review_message_id
            FROM evidence_messages
            WHERE thread_id=?
            """,
            (str(thread_id),)
        ).fetchone()
    return row

def get_evidence_review_message_id(message_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT review_message_id FROM evidence_messages WHERE message_id=?",
            (str(message_id),)
        ).fetchone()
    return row[0] if row else None

def add_evidence_participants(message_id: str, participants):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status, target_snapshot_id FROM evidence_messages WHERE message_id=?",
            (message_id,)
        ).fetchone()
        if not row or row[0] != "pending":
            return False
        target_snapshot_id = row[1]
        for participant in participants:
            participant_id, participant_name, cantidad = normalize_participant_entry(participant)
            if not target_snapshot_id:
                conn.execute(
                    "INSERT OR IGNORE INTO scouts (user_id, username) VALUES (?, ?)",
                    (str(participant_id), participant_name)
                )
                conn.execute("UPDATE scouts SET username=? WHERE user_id=?", (participant_name, str(participant_id)))
            conn.execute(
                """
                INSERT OR IGNORE INTO evidence_participants (message_id, user_id, username, cantidad)
                VALUES (?,?,?,?)
                """,
                (message_id, str(participant_id), participant_name, cantidad)
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

def get_evidence_participants_with_quantity(message_id: str):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id, username, cantidad FROM evidence_participants WHERE message_id=?",
            (message_id,)
        ).fetchall()
    return rows

def get_ranking_snapshot(snapshot_id: int):
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT s.id, s.created_at, s.period_start, s.period_end, s.reason,
                   COUNT(r.user_id) AS total_rows
            FROM ranking_snapshots s
            LEFT JOIN ranking_snapshot_rows r ON r.snapshot_id = s.id
            WHERE s.id=?
            GROUP BY s.id
            """,
            (int(snapshot_id),),
        ).fetchone()
    return row

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
            """
            SELECT user_id, actividad, puntos, status, review_message_id, target_snapshot_id
            FROM evidence_messages
            WHERE message_id=?
            """,
            (message_id,)
        ).fetchone()
        if not row or row[3] != "pending":
            return None
        user_id, actividad, _, _, review_message_id, target_snapshot_id = row
        puntos = get_puntos(actividad)
        participants = conn.execute(
            "SELECT user_id, username, cantidad FROM evidence_participants WHERE message_id=?",
            (message_id,)
        ).fetchall()
        if not participants:
            scout = conn.execute("SELECT username FROM scouts WHERE user_id=?", (user_id,)).fetchone()
            participants = [(user_id, scout[0] if scout else user_id, 1)]

        for participant_id, username, cantidad in participants:
            cantidad = max(1, int(cantidad or 1))
            if target_snapshot_id:
                _apply_snapshot_activity(
                    conn,
                    int(target_snapshot_id),
                    str(participant_id),
                    username,
                    actividad,
                    cantidad,
                    puntos,
                )
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO scouts (user_id, username) VALUES (?, ?)",
                    (str(participant_id), username)
                )
                conn.execute("UPDATE scouts SET username=? WHERE user_id=?", (username, str(participant_id)))
                conn.execute(f"UPDATE scouts SET {actividad} = {actividad} + ? WHERE user_id=?", (cantidad, participant_id))
                conn.execute(
                    "INSERT INTO logs (user_id, username, actividad, cantidad, puntos, fecha, accion) VALUES (?,?,?,?,?,?,?)",
                    (participant_id, username, actividad, cantidad, puntos * cantidad, datetime.utcnow().isoformat(), "evidencia_aprobada")
                )
        conn.execute(
            "UPDATE evidence_messages SET status='approved' WHERE message_id=?",
            (message_id,)
        )
        conn.commit()
    return user_id, actividad, puntos, review_message_id, target_snapshot_id

def move_evidence_to_snapshot(message_id: str, snapshot_id: int):
    snapshot = get_ranking_snapshot(snapshot_id)
    if not snapshot:
        return {"ok": False, "reason": "snapshot_not_found"}

    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT user_id, actividad, puntos, status, review_message_id, target_snapshot_id
            FROM evidence_messages
            WHERE message_id=?
            """,
            (str(message_id),),
        ).fetchone()
        if not row:
            return {"ok": False, "reason": "evidence_not_found"}

        user_id, actividad, stored_points, status, review_message_id, target_snapshot_id = row
        if target_snapshot_id:
            return {
                "ok": False,
                "reason": "already_snapshot",
                "snapshot_id": int(target_snapshot_id),
            }
        if status == "rejected":
            return {"ok": False, "reason": "rejected"}
        if actividad not in ACTIVITY_COLUMNS:
            return {"ok": False, "reason": "invalid_activity", "activity": actividad}

        if status == "pending":
            conn.execute(
                "UPDATE evidence_messages SET target_snapshot_id=? WHERE message_id=?",
                (int(snapshot_id), str(message_id)),
            )
            conn.commit()
            return {
                "ok": True,
                "status": "pending_retargeted",
                "snapshot_id": int(snapshot_id),
                "activity": actividad,
                "participants": [],
                "units": 0,
                "points": 0,
            }

        if status != "approved":
            return {"ok": False, "reason": "invalid_status", "status": status}

        points_unit = int(stored_points or get_puntos(actividad) or 0)
        participants = conn.execute(
            "SELECT user_id, username, cantidad FROM evidence_participants WHERE message_id=?",
            (str(message_id),),
        ).fetchall()
        if not participants:
            scout = conn.execute("SELECT username FROM scouts WHERE user_id=?", (user_id,)).fetchone()
            participants = [(user_id, scout[0] if scout else user_id, 1)]

        moved = []
        total_units = 0
        total_points = 0
        for participant_id, username, cantidad in participants:
            participant_id = str(participant_id)
            cantidad = max(1, int(cantidad or 1))
            current_row = conn.execute(
                f"SELECT {actividad} FROM scouts WHERE user_id=?",
                (participant_id,),
            ).fetchone()
            removed_units = min(cantidad, int(current_row[0] or 0)) if current_row else 0
            if current_row:
                conn.execute(
                    f"UPDATE scouts SET {actividad}=MAX(0, {actividad} - ?) WHERE user_id=?",
                    (cantidad, participant_id),
                )
            if removed_units:
                conn.execute(
                    "INSERT INTO logs (user_id, username, actividad, cantidad, puntos, fecha, accion) VALUES (?,?,?,?,?,?,?)",
                    (
                        participant_id,
                        username,
                        actividad,
                        removed_units,
                        points_unit * removed_units,
                        datetime.utcnow().isoformat(),
                        f"mover_cierre_{snapshot_id}_resta_actual",
                    ),
                )

            _apply_snapshot_activity(
                conn,
                int(snapshot_id),
                participant_id,
                username,
                actividad,
                cantidad,
                points_unit,
            )
            moved.append({
                "user_id": participant_id,
                "username": username,
                "units": cantidad,
                "removed_units": removed_units,
                "points": points_unit * cantidad,
            })
            total_units += cantidad
            total_points += points_unit * cantidad

        conn.execute(
            "UPDATE evidence_messages SET target_snapshot_id=? WHERE message_id=?",
            (int(snapshot_id), str(message_id)),
        )
        conn.commit()

    return {
        "ok": True,
        "status": "approved_moved",
        "snapshot_id": int(snapshot_id),
        "activity": actividad,
        "participants": moved,
        "units": total_units,
        "points": total_points,
        "review_message_id": review_message_id,
    }

def _apply_snapshot_activity(conn, snapshot_id: int, user_id: str, username: str, actividad: str, cantidad: int, puntos_unit: int):
    if actividad not in ACTIVITY_COLUMNS:
        raise ValueError(f"Actividad no valida para cierre: {actividad}")

    cantidad = max(1, int(cantidad or 1))
    delta_points = int(puntos_unit or 0) * cantidad
    row = conn.execute(
        """
        SELECT total_puntos
        FROM ranking_snapshot_rows
        WHERE snapshot_id=? AND user_id=?
        """,
        (snapshot_id, user_id),
    ).fetchone()

    if row:
        total_points = int(row[0] or 0) + delta_points
        nivel, beneficio = get_nivel(total_points)
        conn.execute(
            f"""
            UPDATE ranking_snapshot_rows
            SET username=?, {actividad}=COALESCE({actividad}, 0) + ?,
                total_puntos=?, nivel=?, beneficio=?
            WHERE snapshot_id=? AND user_id=?
            """,
            (username, cantidad, total_points, nivel, beneficio, snapshot_id, user_id),
        )
    else:
        counts = {column: 0 for column in ACTIVITY_COLUMNS}
        counts[actividad] = cantidad
        nivel, beneficio = get_nivel(delta_points)
        conn.execute(
            """
            INSERT INTO ranking_snapshot_rows (
                snapshot_id, user_id, username, kill_scout, kill_pelea,
                limpieza_aspecto, scouteo, mapeo, total_puntos, nivel, beneficio
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                user_id,
                username,
                counts["kill_scout"],
                counts["kill_pelea"],
                counts["limpieza_aspecto"],
                counts["scouteo"],
                counts["mapeo"],
                delta_points,
                nivel,
                beneficio,
            ),
        )

    conn.execute(
        "INSERT INTO logs (user_id, username, actividad, cantidad, puntos, fecha, accion) VALUES (?,?,?,?,?,?,?)",
        (
            user_id,
            username,
            actividad,
            cantidad,
            delta_points,
            datetime.utcnow().isoformat(),
            f"cierre_{snapshot_id}_aprobado",
        ),
    )

def normalize_participant_entry(participant):
    if len(participant) >= 3:
        participant_id, participant_name, cantidad = participant[:3]
    else:
        participant_id, participant_name = participant[:2]
        cantidad = 1
    return str(participant_id), participant_name, max(1, int(cantidad or 1))

def find_scout_by_name(name: str):
    target = normalize_name(name)
    with get_conn() as conn:
        rows = conn.execute("SELECT user_id, username FROM scouts").fetchall()
    for user_id, username in rows:
        if normalize_name(username) == target:
            return user_id, username
    return None

def add_scout_alias(user_id: str, username: str, alias: str):
    normalized_alias = normalize_name(alias)
    if not normalized_alias:
        return False

    ensure_scout(user_id, username)
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO scout_aliases
                (normalized_alias, alias, user_id, username, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (normalized_alias, alias.strip(), str(user_id), username, datetime.utcnow().isoformat())
        )
        conn.commit()
    return True

def remove_scout_alias(alias: str):
    normalized_alias = normalize_name(alias)
    with get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM scout_aliases WHERE normalized_alias=?",
            (normalized_alias,)
        )
        conn.commit()
    return cursor.rowcount > 0

def find_scout_alias(name: str):
    normalized_alias = normalize_name(name)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id, username, alias FROM scout_aliases WHERE normalized_alias=?",
            (normalized_alias,)
        ).fetchone()
    return row

def get_scout_aliases(user_id: str | None = None):
    with get_conn() as conn:
        if user_id:
            rows = conn.execute(
                "SELECT user_id, username, alias FROM scout_aliases WHERE user_id=? ORDER BY alias",
                (str(user_id),)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT user_id, username, alias FROM scout_aliases ORDER BY username, alias"
            ).fetchall()
    return rows

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

def create_ranking_snapshot(period_start=None, period_end=None, reason: str = "manual"):
    rows = get_all_scouts()
    if not rows:
        return None

    created_at = datetime.utcnow().isoformat()
    period_start_text = isoformat_or_text(period_start)
    period_end_text = isoformat_or_text(period_end) or created_at
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO ranking_snapshots (created_at, period_start, period_end, reason)
            VALUES (?, ?, ?, ?)
            """,
            (created_at, period_start_text, period_end_text, reason),
        )
        snapshot_id = cursor.lastrowid
        for row in rows:
            points = calc_puntos_totales(row)
            nivel, beneficio = get_nivel(points)
            conn.execute(
                """
                INSERT INTO ranking_snapshot_rows (
                    snapshot_id, user_id, username, kill_scout, kill_pelea,
                    limpieza_aspecto, scouteo, mapeo, total_puntos, nivel, beneficio
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (snapshot_id, *row, points, nivel, beneficio),
            )
        conn.commit()
    return snapshot_id

def get_latest_ranking_snapshot():
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT s.id, s.created_at, s.period_start, s.period_end, s.reason,
                   COUNT(r.user_id) AS total_rows
            FROM ranking_snapshots s
            LEFT JOIN ranking_snapshot_rows r ON r.snapshot_id = s.id
            GROUP BY s.id
            ORDER BY s.id DESC
            LIMIT 1
            """
        ).fetchone()
    return row

def get_ranking_snapshot_for_time(moment):
    moment_text = isoformat_or_text(moment)
    if not moment_text:
        return None
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT s.id, s.created_at, s.period_start, s.period_end, s.reason,
                   COUNT(r.user_id) AS total_rows
            FROM ranking_snapshots s
            LEFT JOIN ranking_snapshot_rows r ON r.snapshot_id = s.id
            WHERE (s.period_start IS NULL OR s.period_start <= ?)
              AND (s.period_end IS NULL OR s.period_end >= ?)
            GROUP BY s.id
            ORDER BY s.id DESC
            LIMIT 1
            """,
            (moment_text, moment_text),
        ).fetchone()
    return row

def get_ranking_snapshot_rows(snapshot_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT user_id, username, kill_scout, kill_pelea, limpieza_aspecto,
                   scouteo, mapeo, total_puntos, nivel, beneficio
            FROM ranking_snapshot_rows
            WHERE snapshot_id=?
            ORDER BY total_puntos DESC, username
            """,
            (snapshot_id,),
        ).fetchall()
    return rows

def isoformat_or_text(value):
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)

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
        rows = conn.execute(
            "SELECT actividad, puntos FROM config WHERE actividad != ?",
            (CONFIG_REPAIR_MARKER,)
        ).fetchall()
    return rows

def get_bot_state(key: str):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM bot_state WHERE key=?", (key,)).fetchone()
    return row[0] if row else None

def set_bot_state(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)",
            (key, value)
        )
        conn.commit()

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
    points = max(0, int(puntos or 0))
    for priority in PRIORITY_LEVELS:
        max_points = priority["max"]
        if points >= priority["min"] and (max_points is None or points <= max_points):
            return priority["level"], priority["benefit"]
    return "Inactivo", "Sin prioridad"

def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name or ""))
    return "".join(ch.lower() for ch in text if ch.isalnum())
