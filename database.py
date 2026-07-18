import os
import sqlite3
import unicodedata
import json
from datetime import datetime, timezone

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
DEFAULT_PRIORITY_MIN_POINTS = 50
PRIORITY_MIN_STATE_KEY = "priority_min_points"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


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
                mapeo INTEGER DEFAULT 0,
                points_adjustment INTEGER DEFAULT 0
            )
        """)
        c.execute("PRAGMA table_info(scouts)")
        scout_cols = [row[1] for row in c.fetchall()]
        if "kill_pelea" not in scout_cols:
            c.execute("ALTER TABLE scouts ADD COLUMN kill_pelea INTEGER DEFAULT 0")
        if "points_adjustment" not in scout_cols:
            c.execute("ALTER TABLE scouts ADD COLUMN points_adjustment INTEGER DEFAULT 0")
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
                points_override INTEGER,
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
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                category TEXT NOT NULL,
                action TEXT NOT NULL,
                actor_id TEXT,
                actor_name TEXT,
                target_type TEXT,
                target_id TEXT,
                summary TEXT,
                details_json TEXT
            )
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_events_created_at
            ON audit_events(created_at DESC)
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS scouteo_balances (
                scope TEXT,
                user_id TEXT,
                minutes INTEGER DEFAULT 0,
                maps INTEGER DEFAULT 0,
                PRIMARY KEY (scope, user_id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS scouteo_contributions (
                message_id TEXT,
                user_id TEXT,
                minutes INTEGER DEFAULT 0,
                maps INTEGER DEFAULT 0,
                hours_required INTEGER DEFAULT 4,
                maps_per_unit INTEGER DEFAULT 3,
                multiplier_hundredths INTEGER DEFAULT 100,
                PRIMARY KEY (message_id, user_id)
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
        if "points_override" not in participant_cols:
            c.execute("ALTER TABLE evidence_participants ADD COLUMN points_override INTEGER")
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
            (user_id, username, actividad, cantidad, total_puntos, utc_now_iso(), "suma")
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
    fecha = utc_now_iso()
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
            row = conn.execute(
                "SELECT status FROM evidence_messages WHERE message_id=?",
                (message_id,)
            ).fetchone()
            if not row or row[0] != "rejected":
                return 0
            conn.execute("DELETE FROM evidence_participants WHERE message_id=?", (message_id,))
            conn.execute("DELETE FROM evidence_messages WHERE message_id=?", (message_id,))
            conn.execute(
                """
                INSERT INTO evidence_messages (
                    message_id, user_id, actividad, puntos, fecha, status, target_snapshot_id
                )
                VALUES (?,?,?,?,?,?,?)
                """,
                (message_id, user_id, actividad, puntos_unit, fecha, "pending", target_snapshot_id)
            )
        for participant in participants:
            participant_id, participant_name, cantidad = normalize_participant_entry(participant)
            points_override = int(participant[3]) if len(participant) >= 4 and participant[3] is not None else None
            if not target_snapshot_id:
                conn.execute(
                    "INSERT OR IGNORE INTO scouts (user_id, username) VALUES (?, ?)",
                    (str(participant_id), participant_name)
                )
                conn.execute("UPDATE scouts SET username=? WHERE user_id=?", (participant_name, str(participant_id)))
            conn.execute(
                """
                INSERT OR IGNORE INTO evidence_participants (message_id, user_id, username, cantidad, points_override)
                VALUES (?,?,?,?,?)
                """,
                (message_id, str(participant_id), participant_name, cantidad, points_override)
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


def get_recent_evidence(limit: int = 3):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                e.message_id,
                e.actividad,
                e.status,
                e.fecha,
                e.target_snapshot_id,
                COUNT(ep.user_id) AS participant_count,
                COALESCE(
                    SUM(
                        CASE
                            WHEN ep.user_id IS NULL THEN e.puntos
                            ELSE COALESCE(
                                ep.points_override,
                                e.puntos * CASE WHEN ep.cantidad > 0 THEN ep.cantidad ELSE 0 END
                            )
                        END
                    ),
                    e.puntos
                ) AS total_points
            FROM evidence_messages e
            LEFT JOIN evidence_participants ep ON ep.message_id = e.message_id
            GROUP BY e.message_id
            ORDER BY e.fecha DESC, e.rowid DESC
            LIMIT ?
            """,
            (max(1, min(10, int(limit))),),
        ).fetchall()
    return [
        {
            "message_id": str(row[0]),
            "activity": row[1],
            "status": row[2],
            "created_at": row[3],
            "target_snapshot_id": row[4],
            "participants": max(1, int(row[5] or 0)),
            "points": int(row[6] or 0),
        }
        for row in rows
    ]


def record_audit_event(
    category: str,
    action: str,
    actor_id: str | None = None,
    actor_name: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    summary: str | None = None,
    details: dict | None = None,
    created_at: str | None = None,
):
    timestamp = created_at or datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    payload = json.dumps(details or {}, ensure_ascii=False, sort_keys=True)
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO audit_events (
                created_at, category, action, actor_id, actor_name,
                target_type, target_id, summary, details_json
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                timestamp,
                str(category or "sistema"),
                str(action or "evento"),
                str(actor_id) if actor_id is not None else None,
                str(actor_name) if actor_name is not None else None,
                str(target_type) if target_type is not None else None,
                str(target_id) if target_id is not None else None,
                str(summary or ""),
                payload,
            ),
        )
        conn.commit()
        return cursor.lastrowid

def get_audit_events(limit: int | None = 500, category: str | None = None):
    query = """
        SELECT id, created_at, category, action, actor_id, actor_name,
               target_type, target_id, summary, details_json
        FROM audit_events
    """
    params = []
    if category:
        query += " WHERE category=?"
        params.append(str(category))
    query += " ORDER BY id DESC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(max(1, int(limit)))

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    events = []
    for row in rows:
        try:
            details = json.loads(row[9] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            details = {"raw": row[9]}
        events.append({
            "id": int(row[0]),
            "created_at": row[1],
            "category": row[2],
            "action": row[3],
            "actor_id": row[4],
            "actor_name": row[5],
            "target_type": row[6],
            "target_id": row[7],
            "summary": row[8] or "",
            "details": details,
        })
    return events

def get_evidence_summary(message_id: str):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT message_id, user_id, actividad, puntos, status, review_message_id, target_snapshot_id
            FROM evidence_messages
            WHERE message_id=?
            """,
            (str(message_id),),
        ).fetchone()

def _scouteo_scope(target_snapshot_id=None) -> str:
    return f"snapshot:{int(target_snapshot_id)}" if target_snapshot_id else "current"

def get_scouteo_projection(
    user_id: str,
    minutes: int,
    maps: int,
    hours_required: int = 4,
    maps_per_unit: int = 3,
    target_snapshot_id=None,
):
    scope = _scouteo_scope(target_snapshot_id)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT minutes, maps FROM scouteo_balances WHERE scope=? AND user_id=?",
            (scope, str(user_id)),
        ).fetchone()
    previous_minutes, previous_maps = row or (0, 0)
    total_minutes = max(0, int(previous_minutes or 0) + int(minutes or 0))
    total_maps = max(0, int(previous_maps or 0) + int(maps or 0))
    eligible = total_minutes >= max(1, int(hours_required)) * 60
    units = total_maps // max(1, int(maps_per_unit)) if eligible else 0
    return {
        "previous_minutes": int(previous_minutes or 0),
        "previous_maps": int(previous_maps or 0),
        "total_minutes": total_minutes,
        "total_maps": total_maps,
        "eligible": eligible,
        "units": units,
        "remaining_maps": total_maps - (units * max(1, int(maps_per_unit))),
    }

def set_scouteo_contributions(message_id: str, contributions):
    with get_conn() as conn:
        conn.execute("DELETE FROM scouteo_contributions WHERE message_id=?", (str(message_id),))
        conn.executemany(
            """
            INSERT INTO scouteo_contributions (
                message_id, user_id, minutes, maps, hours_required,
                maps_per_unit, multiplier_hundredths
            ) VALUES (?,?,?,?,?,?,?)
            """,
            [
                (
                    str(message_id), str(item[0]), max(0, int(item[1])), max(0, int(item[2])),
                    max(1, int(item[3])), max(1, int(item[4])),
                    max(70, min(100, int(item[5]))),
                )
                for item in contributions
            ],
        )
        conn.commit()

def get_scouteo_review_rows(message_id: str):
    with get_conn() as conn:
        evidence = conn.execute(
            "SELECT target_snapshot_id, status FROM evidence_messages WHERE message_id=? AND actividad='scouteo'",
            (str(message_id),),
        ).fetchone()
        if not evidence:
            return []
        target_snapshot_id, status = evidence
        rows = conn.execute(
            """
            SELECT c.user_id, COALESCE(p.username, c.user_id), c.minutes, c.maps,
                   c.hours_required, c.maps_per_unit, c.multiplier_hundredths
            FROM scouteo_contributions c
            LEFT JOIN evidence_participants p
              ON p.message_id=c.message_id AND p.user_id=c.user_id
            WHERE c.message_id=?
            ORDER BY LOWER(COALESCE(p.username, c.user_id))
            """,
            (str(message_id),),
        ).fetchall()

    points_per_unit = get_puntos("scouteo")
    result = []
    for user_id, username, minutes, maps, hours_required, maps_per_unit, multiplier in rows:
        projection = get_scouteo_projection(
            str(user_id),
            int(minutes or 0),
            int(maps or 0),
            int(hours_required or 1),
            int(maps_per_unit or 1),
            target_snapshot_id,
        )
        units = projection["units"]
        exact_points = (units * max(0, int(points_per_unit)) * int(multiplier) + 50) // 100
        result.append({
            "user_id": str(user_id),
            "username": username,
            "minutes": int(minutes or 0),
            "maps": int(maps or 0),
            "hours_required": int(hours_required or 1),
            "maps_per_unit": int(maps_per_unit or 1),
            "multiplier_hundredths": int(multiplier or 100),
            "units": units,
            "points": exact_points,
            "accumulated_minutes": projection["total_minutes"],
            "accumulated_maps": projection["total_maps"],
            "status": status,
        })
    return result

def set_scouteo_review_multiplier(message_id: str, user_id: str, multiplier_hundredths: int):
    multiplier = max(70, min(100, int(multiplier_hundredths)))
    with get_conn() as conn:
        evidence = conn.execute(
            "SELECT status FROM evidence_messages WHERE message_id=? AND actividad='scouteo'",
            (str(message_id),),
        ).fetchone()
        if not evidence:
            return {"ok": False, "reason": "not_found"}
        if evidence[0] != "pending":
            return {"ok": False, "reason": "already_reviewed"}
        cursor = conn.execute(
            """
            UPDATE scouteo_contributions
            SET multiplier_hundredths=?
            WHERE message_id=? AND user_id=?
            """,
            (multiplier, str(message_id), str(user_id)),
        )
        if cursor.rowcount <= 0:
            return {"ok": False, "reason": "participant_not_found"}
        conn.commit()

    row = next(
        (item for item in get_scouteo_review_rows(message_id) if item["user_id"] == str(user_id)),
        None,
    )
    return {"ok": True, "row": row}

def _apply_scouteo_contributions(conn, message_id: str, target_snapshot_id, points_per_unit: int):
    rows = conn.execute(
        """
        SELECT user_id, minutes, maps, hours_required, maps_per_unit, multiplier_hundredths
        FROM scouteo_contributions WHERE message_id=?
        """,
        (str(message_id),),
    ).fetchall()
    scope = _scouteo_scope(target_snapshot_id)
    calculated = {}
    for user_id, minutes, maps, hours_required, maps_per_unit, multiplier in rows:
        balance = conn.execute(
            "SELECT minutes, maps FROM scouteo_balances WHERE scope=? AND user_id=?",
            (scope, str(user_id)),
        ).fetchone() or (0, 0)
        total_minutes = max(0, int(balance[0] or 0) + int(minutes or 0))
        total_maps = max(0, int(balance[1] or 0) + int(maps or 0))
        eligible = total_minutes >= max(1, int(hours_required)) * 60
        units = total_maps // max(1, int(maps_per_unit)) if eligible else 0
        remaining_maps = total_maps - units * max(1, int(maps_per_unit))
        exact_points = (units * max(0, int(points_per_unit)) * int(multiplier) + 50) // 100
        conn.execute(
            """
            INSERT INTO scouteo_balances (scope, user_id, minutes, maps)
            VALUES (?,?,?,?)
            ON CONFLICT(scope, user_id) DO UPDATE SET minutes=excluded.minutes, maps=excluded.maps
            """,
            (scope, str(user_id), total_minutes, remaining_maps),
        )
        conn.execute(
            "UPDATE evidence_participants SET cantidad=?, points_override=? WHERE message_id=? AND user_id=?",
            (units, exact_points, str(message_id), str(user_id)),
        )
        calculated[str(user_id)] = (units, exact_points)
    return calculated

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
        scouteo_calculated = (
            _apply_scouteo_contributions(conn, message_id, target_snapshot_id, puntos)
            if actividad == "scouteo" else {}
        )
        participants = conn.execute(
            "SELECT user_id, username, cantidad, points_override FROM evidence_participants WHERE message_id=?",
            (message_id,)
        ).fetchall()
        if not participants:
            scout = conn.execute("SELECT username FROM scouts WHERE user_id=?", (user_id,)).fetchone()
            participants = [(user_id, scout[0] if scout else user_id, 1, None)]

        for participant_id, username, cantidad, points_override in participants:
            if str(participant_id) in scouteo_calculated:
                cantidad, points_override = scouteo_calculated[str(participant_id)]
                cantidad = max(0, int(cantidad or 0))
            else:
                cantidad = max(1, int(cantidad or 1))
            exact_points = int(points_override) if points_override is not None else puntos * cantidad
            if cantidad == 0:
                continue
            if target_snapshot_id:
                _apply_snapshot_activity(
                    conn,
                    int(target_snapshot_id),
                    str(participant_id),
                    username,
                    actividad,
                    cantidad,
                    puntos,
                    exact_points,
                )
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO scouts (user_id, username) VALUES (?, ?)",
                    (str(participant_id), username)
                )
                conn.execute("UPDATE scouts SET username=? WHERE user_id=?", (username, str(participant_id)))
                conn.execute(f"UPDATE scouts SET {actividad} = {actividad} + ? WHERE user_id=?", (cantidad, participant_id))
                conn.execute(
                    "UPDATE scouts SET points_adjustment = points_adjustment + ? WHERE user_id=?",
                    (exact_points - (puntos * cantidad), participant_id),
                )
                conn.execute(
                    "INSERT INTO logs (user_id, username, actividad, cantidad, puntos, fecha, accion) VALUES (?,?,?,?,?,?,?)",
                    (participant_id, username, actividad, cantidad, exact_points, utc_now_iso(), "evidencia_aprobada")
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
            "SELECT user_id, username, cantidad, points_override FROM evidence_participants WHERE message_id=?",
            (str(message_id),),
        ).fetchall()
        if not participants:
            scout = conn.execute("SELECT username FROM scouts WHERE user_id=?", (user_id,)).fetchone()
            participants = [(user_id, scout[0] if scout else user_id, 1, None)]

        moved = []
        total_units = 0
        total_points = 0
        for participant_id, username, cantidad, points_override in participants:
            participant_id = str(participant_id)
            cantidad = max(1, int(cantidad or 1))
            exact_points = int(points_override) if points_override is not None else points_unit * cantidad
            evidence_adjustment = exact_points - (points_unit * cantidad)
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
                conn.execute(
                    "UPDATE scouts SET points_adjustment = points_adjustment - ? WHERE user_id=?",
                    (evidence_adjustment, participant_id),
                )
            if removed_units:
                conn.execute(
                    "INSERT INTO logs (user_id, username, actividad, cantidad, puntos, fecha, accion) VALUES (?,?,?,?,?,?,?)",
                    (
                        participant_id,
                        username,
                        actividad,
                        removed_units,
                        exact_points,
                        utc_now_iso(),
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
                exact_points,
            )
            moved.append({
                "user_id": participant_id,
                "username": username,
                "units": cantidad,
                "removed_units": removed_units,
                "points": exact_points,
            })
            total_units += cantidad
            total_points += exact_points

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

def _apply_snapshot_activity(
    conn,
    snapshot_id: int,
    user_id: str,
    username: str,
    actividad: str,
    cantidad: int,
    puntos_unit: int,
    points_override: int | None = None,
):
    if actividad not in ACTIVITY_COLUMNS:
        raise ValueError(f"Actividad no valida para cierre: {actividad}")

    cantidad = max(1, int(cantidad or 1))
    delta_points = int(points_override) if points_override is not None else int(puntos_unit or 0) * cantidad
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
            utc_now_iso(),
            f"cierre_{snapshot_id}_aprobado",
        ),
    )

def adjust_snapshot_activity(snapshot_id: int, user_id: str, username: str, actividad: str, cantidad: int):
    if actividad not in ACTIVITY_COLUMNS:
        raise ValueError(f"Actividad no valida para cierre: {actividad}")

    snapshot = get_ranking_snapshot(snapshot_id)
    if not snapshot:
        return {"ok": False, "reason": "snapshot_not_found"}

    quantity = int(cantidad or 0)
    if quantity == 0:
        return {"ok": False, "reason": "zero_quantity"}

    points_unit = get_puntos(actividad)
    with get_conn() as conn:
        row = conn.execute(
            f"""
            SELECT kill_scout, kill_pelea, limpieza_aspecto, scouteo, mapeo, total_puntos
            FROM ranking_snapshot_rows
            WHERE snapshot_id=? AND user_id=?
            """,
            (int(snapshot_id), str(user_id)),
        ).fetchone()

        if quantity > 0:
            applied_units = quantity
            delta_points = points_unit * applied_units
            if row:
                total_points = int(row[5] or 0) + delta_points
                nivel, beneficio = get_nivel(total_points)
                conn.execute(
                    f"""
                    UPDATE ranking_snapshot_rows
                    SET username=?, {actividad}=COALESCE({actividad}, 0) + ?,
                        total_puntos=?, nivel=?, beneficio=?
                    WHERE snapshot_id=? AND user_id=?
                    """,
                    (username, applied_units, total_points, nivel, beneficio, int(snapshot_id), str(user_id)),
                )
            else:
                counts = {column: 0 for column in ACTIVITY_COLUMNS}
                counts[actividad] = applied_units
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
                        int(snapshot_id),
                        str(user_id),
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
            action = f"cierre_{snapshot_id}_ajuste_suma"
        else:
            if not row:
                return {"ok": False, "reason": "snapshot_user_not_found"}

            activity_index = ACTIVITY_COLUMNS.index(actividad)
            current_units = int(row[activity_index] or 0)
            applied_units = min(abs(quantity), current_units)
            if applied_units <= 0:
                return {"ok": False, "reason": "nothing_to_subtract"}

            delta_points = points_unit * applied_units
            total_points = max(0, int(row[5] or 0) - delta_points)
            nivel, beneficio = get_nivel(total_points)
            conn.execute(
                f"""
                UPDATE ranking_snapshot_rows
                SET username=?, {actividad}=MAX(0, COALESCE({actividad}, 0) - ?),
                    total_puntos=?, nivel=?, beneficio=?
                WHERE snapshot_id=? AND user_id=?
                """,
                (username, applied_units, total_points, nivel, beneficio, int(snapshot_id), str(user_id)),
            )
            action = f"cierre_{snapshot_id}_ajuste_resta"

        conn.execute(
            "INSERT INTO logs (user_id, username, actividad, cantidad, puntos, fecha, accion) VALUES (?,?,?,?,?,?,?)",
            (
                str(user_id),
                username,
                actividad,
                applied_units,
                delta_points,
                utc_now_iso(),
                action,
            ),
        )
        conn.commit()

    return {
        "ok": True,
        "snapshot_id": int(snapshot_id),
        "user_id": str(user_id),
        "username": username,
        "activity": actividad,
        "requested_units": quantity,
        "applied_units": applied_units,
        "points": delta_points,
        "action": "sumar" if quantity > 0 else "restar",
    }

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
            (normalized_alias, alias.strip(), str(user_id), username, utc_now_iso())
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
            (user_id, username, actividad, real_sub, total_puntos, utc_now_iso(), "resta")
        )
        conn.commit()
    return total_puntos

def reset_all():
    with get_conn() as conn:
        conn.execute("DELETE FROM scouts")
        conn.execute("DELETE FROM scouteo_balances WHERE scope='current'")
        conn.commit()

def create_ranking_snapshot(period_start=None, period_end=None, reason: str = "manual"):
    rows = get_all_scouts()
    if not rows:
        return None

    created_at = utc_now_iso()
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

def get_priority_min_points(default: int = DEFAULT_PRIORITY_MIN_POINTS) -> int:
    raw = get_bot_state(PRIORITY_MIN_STATE_KEY)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return max(0, int(default))

def set_priority_min_points(points: int) -> int:
    value = max(0, int(points))
    set_bot_state(PRIORITY_MIN_STATE_KEY, str(value))
    return value

def get_prio_status(puntos: int, minimum: int | None = None):
    points = max(0, int(puntos or 0))
    cutoff = get_priority_min_points() if minimum is None else max(0, int(minimum))
    qualifies = points >= cutoff
    return {
        "points": points,
        "minimum": cutoff,
        "qualifies": qualifies,
        "missing": max(0, cutoff - points),
    }

# ── Helpers ───────────────────────────────────────────────────────────────────

COLS = ["kill_scout","kill_pelea","limpieza_aspecto","scouteo","mapeo"]

def scout_select_sql(where_clause: str = ""):
    return "SELECT user_id, username, kill_scout, kill_pelea, limpieza_aspecto, scouteo, mapeo FROM scouts " + where_clause

def get_points_adjustment(user_id: str) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT points_adjustment FROM scouts WHERE user_id=?", (str(user_id),)).fetchone()
    return int(row[0] or 0) if row else 0

def calc_puntos_totales(row) -> int:
    config = {a: p for a, p in get_all_config()}
    total = 0
    for i, col in enumerate(COLS):
        total += row[i + 2] * config.get(col, 0)
    return max(0, total + get_points_adjustment(row[0]))

def get_nivel(puntos: int) -> tuple[str, str]:
    """Compatibilidad con cierres antiguos; RankingBot ya no usa niveles."""
    status = get_prio_status(puntos)
    if status["qualifies"]:
        return "Con prio", f"Cumple el corte de {status['minimum']} puntos"
    return "Sin prio", f"Le faltan {status['missing']} puntos"

def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name or ""))
    return "".join(ch.lower() for ch in text if ch.isalnum())
