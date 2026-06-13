import csv
import io
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import timezone


ROAD_SCORE = 1.0
PRIORITY_SCORE = 0.15
RELOCK_SCORE = 0.15
DUPLICATE_SCORE = 0.0

EVENT_LABELS = {
    "road": ("road added",),
    "priority": ("priority added",),
    "relock": ("relock added",),
}


@dataclass
class MappingEvent:
    event_type: str
    player: str
    discord_id: str
    created_at: str
    source_message_id: str
    source_url: str
    from_map: str = ""
    to_map: str = ""
    map_name: str = ""
    link_id: str = ""


def parse_mapping_message(message):
    text = message_to_text(message)
    event_type = detect_event_type(text)
    if not event_type:
        return None

    fields = message_fields(message)
    player_text = field_value(fields, "Created By", "Added By") or labeled_value(text, "Created By", "Added By")
    player, discord_id = parse_player(player_text)

    created_at = getattr(message, "created_at", None)
    if created_at:
        created_at = created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    else:
        created_at = labeled_value(text, "Created", "Time", "Date")

    event = MappingEvent(
        event_type=event_type,
        player=player or "Desconocido",
        discord_id=discord_id,
        created_at=created_at or "",
        source_message_id=str(getattr(message, "id", "")),
        source_url=getattr(message, "jump_url", ""),
    )

    if event_type == "road":
        event.from_map = clean_map_value(field_value(fields, "From") or labeled_value(text, "From"))
        event.to_map = clean_map_value(field_value(fields, "To") or labeled_value(text, "To"))
        event.link_id = clean_value(field_value(fields, "Link ID", "Link") or labeled_value(text, "Link ID", "Link"))
        if not event.from_map or not event.to_map:
            return None
    else:
        event.map_name = clean_map_value(
            field_value(fields, "Map", "Mapa")
            or labeled_value(text, "Map", "Mapa")
        )
        if not event.map_name:
            return None

    return event


def analyze_mapping_events(events):
    stats = {}
    route_events = defaultdict(list)

    for event in events:
        key = player_key(event)
        if key not in stats:
            stats[key] = {
                "player": event.player,
                "discord_id": event.discord_id,
                "road_total": 0,
                "road_unique": 0,
                "road_duplicates": 0,
                "priority": 0,
                "relock": 0,
                "score": 0.0,
                "strategic_score": 0.0,
            }

        if event.event_type == "road":
            stats[key]["road_total"] += 1
            route_events[route_key(event)].append(event)
        elif event.event_type == "priority":
            stats[key]["priority"] += 1
        elif event.event_type == "relock":
            stats[key]["relock"] += 1

    duplicates = []
    for route, route_group in route_events.items():
        first, repeated = route_group[0], route_group[1:]
        stats[player_key(first)]["road_unique"] += 1
        for event in repeated:
            stats[player_key(event)]["road_duplicates"] += 1

        if repeated:
            duplicates.append({
                "from": first.from_map,
                "to": first.to_map,
                "link_ids": [event.link_id for event in route_group if event.link_id],
                "players": [display_player(event) for event in route_group],
                "message_ids": [event.source_message_id for event in route_group],
            })

    for row in stats.values():
        row["strategic_score"] = (row["priority"] * PRIORITY_SCORE) + (row["relock"] * RELOCK_SCORE)
        row["score"] = (
            (row["road_unique"] * ROAD_SCORE)
            + row["strategic_score"]
            + (row["road_duplicates"] * DUPLICATE_SCORE)
        )

    ranking = sorted(
        stats.values(),
        key=lambda row: (-row["score"], -row["road_unique"], -row["strategic_score"], row["player"].lower()),
    )
    for index, row in enumerate(ranking, start=1):
        row["rank"] = index

    return {
        "events": events,
        "ranking": ranking,
        "duplicates": duplicates,
        "summary": build_summary(events, ranking, duplicates),
    }


def build_summary(events, ranking, duplicates):
    roads = [event for event in events if event.event_type == "road"]
    unique_routes = len({route_key(event) for event in roads})
    most_active = max(ranking, key=lambda row: row["road_total"] + row["priority"] + row["relock"], default=None)
    strategic_rows = [row for row in ranking if row["strategic_score"] > 0]
    best_strategic = max(strategic_rows, key=lambda row: row["strategic_score"], default=None)
    return {
        "total_events": len(events),
        "road_total": len(roads),
        "road_unique": unique_routes,
        "road_duplicates": max(0, len(roads) - unique_routes),
        "priority_total": sum(1 for event in events if event.event_type == "priority"),
        "relock_total": sum(1 for event in events if event.event_type == "relock"),
        "most_active": most_active["player"] if most_active else "N/A",
        "best_strategic": best_strategic["player"] if best_strategic else "N/A",
    }


def message_to_text(message):
    parts = [getattr(message, "content", "") or ""]
    for embed in getattr(message, "embeds", []):
        parts.extend([
            getattr(embed, "title", "") or "",
            getattr(embed, "description", "") or "",
            getattr(getattr(embed, "author", None), "name", "") or "",
            getattr(getattr(embed, "footer", None), "text", "") or "",
        ])
        for field in getattr(embed, "fields", []):
            parts.append(getattr(field, "name", "") or "")
            parts.append(str(getattr(field, "value", "") or ""))
    return "\n".join(part for part in parts if part)


def message_fields(message):
    fields = {}
    for embed in getattr(message, "embeds", []):
        for field in getattr(embed, "fields", []):
            fields[normalize_label(getattr(field, "name", ""))] = str(getattr(field, "value", "") or "")
    return fields


def detect_event_type(text):
    normalized = normalize_text(text)
    for event_type, labels in EVENT_LABELS.items():
        if any(label in normalized for label in labels):
            return event_type
    return None


def field_value(fields, *labels):
    for label in labels:
        value = fields.get(normalize_label(label))
        if value:
            return value
    return ""


def labeled_value(text, *labels):
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    normalized_labels = {normalize_label(label) for label in labels}
    for index, line in enumerate(lines):
        normalized = normalize_label(line)
        if normalized in normalized_labels and index + 1 < len(lines):
            return lines[index + 1]
        for label in labels:
            match = re.match(rf"^{re.escape(label)}\s*:?\s*(.+)$", line, re.IGNORECASE)
            if match:
                return match.group(1)
    return ""


def parse_player(text):
    text = clean_value(text)
    if not text:
        return "", ""

    mention = re.search(r"<@\s*!?\s*(\d{15,25})\s*>", text)
    discord_id = mention.group(1) if mention else ""

    paren_id = re.search(r"\(\s*(\d{15,25})\s*\)", text)
    if paren_id:
        discord_id = paren_id.group(1)

    bare_id = re.search(r"\b\d{15,25}\b", text)
    if bare_id and not discord_id:
        discord_id = bare_id.group(0)

    name = re.sub(r"<@\s*!?\s*\d{15,25}\s*>", "", text)
    name = re.sub(r"\(\s*\d{15,25}\s*\)", "", name)
    name = re.sub(r"\b\d{15,25}\b", "", name)
    name = cleanup_player_name(name)
    return name or (f"<@{discord_id}>" if discord_id else text), discord_id


def clean_value(value):
    text = str(value or "")
    text = text.replace("**", "").replace("__", "").replace("`", "")
    return re.sub(r"\s+", " ", text).strip()


def clean_map_value(value):
    text = clean_value(value)
    text = re.sub(r"<a?:[^:]+:\d+>", "", text)
    text = "".join(ch for ch in text if ch.isalnum() or ch in " -'_")
    return re.sub(r"\s+", " ", text).strip()


def cleanup_player_name(value):
    text = clean_value(value)
    text = text.strip(" @()[]{}|:-")
    if not normalize_label(text):
        return ""
    return text


def normalize_text(text):
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(ch.lower() for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text)


def normalize_label(text):
    return re.sub(r"[^a-z0-9]+", "", normalize_text(text))


def normalize_map_name(text):
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(ch.lower() for ch in text if ch.isalnum())
    return text


def route_key(event):
    return normalize_map_name(event.from_map), normalize_map_name(event.to_map)


def player_key(event):
    return event.discord_id or normalize_label(event.player)


def display_player(event):
    return f"{event.player} ({event.discord_id})" if event.discord_id else event.player


def display_row_player(row):
    player = cleanup_player_name(row.get("player", ""))
    if player and not player.startswith("<@"):
        return player
    discord_id = row.get("discord_id", "")
    return f"Usuario {discord_id[-4:]}" if discord_id else "Desconocido"


def format_score(score):
    return f"{score:.2f}".rstrip("0").rstrip(".")


def final_units_for_row(row, top_weight, max_units):
    if top_weight <= 0 or row["score"] <= 0:
        return 0
    return max(1, round((row["score"] / top_weight) * max_units))


def build_ranking_table(ranking, max_units=10, activity_value=1, limit=12):
    if not ranking:
        return "Sin eventos validos."

    top_weight = max((row["score"] for row in ranking), default=0)
    lines = [
        " # Jugador        Uniq Dup Estr Peso Ud Pts",
        "-- -------------- ---- --- ---- ---- -- ---",
    ]
    for row in ranking[:limit]:
        player = display_row_player(row)[:14].ljust(14)
        units = final_units_for_row(row, top_weight, max_units)
        final_points = units * activity_value
        strategic = row["priority"] + row["relock"]
        lines.append(
            f"{str(row['rank']).rjust(2)} {player} "
            f"{str(row['road_unique']).rjust(4)} "
            f"{str(row['road_duplicates']).rjust(3)} "
            f"{str(strategic).rjust(4)} "
            f"{format_score(row['score']).rjust(4)} "
            f"{str(units).rjust(2)} "
            f"{format_score(final_points).rjust(3)}"
        )
    if len(ranking) > limit:
        lines.append(f"... y {len(ranking) - limit} mas")
    return f"```text\n{chr(10).join(lines)}\n```"


def build_duplicates_table(duplicates, limit=8):
    if not duplicates:
        return "No encontre rutas duplicadas."

    lines = []
    for item in duplicates[:limit]:
        link_ids = ", ".join(item["link_ids"]) or "sin Link ID"
        players = ", ".join(item["players"])
        lines.append(f"{item['from']} -> {item['to']} | Links: {link_ids} | {players}")
    if len(duplicates) > limit:
        lines.append(f"... y {len(duplicates) - limit} duplicados mas")
    return "\n".join(lines)[:1000]


def build_duplicates_summary(duplicates, limit=5):
    if not duplicates:
        return "No encontre rutas duplicadas."

    lines = [f"Total rutas duplicadas: `{len(duplicates)}`"]
    for item in duplicates[:limit]:
        repeats = max(0, len(item["message_ids"]) - 1)
        players = unique_preserve_order(item["players"])
        lines.append(
            f"`{item['from']} -> {item['to']}` x{repeats + 1} "
            f"({', '.join(players[:3])})"
        )
    if len(duplicates) > limit:
        lines.append(f"... y {len(duplicates) - limit} mas")
    return "\n".join(lines)[:1000]


def unique_preserve_order(items):
    result = []
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def ranking_csv_bytes(ranking, max_units=10, activity_value=1):
    top_weight = max((row["score"] for row in ranking), default=0)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "puesto",
        "jugador",
        "discord_id",
        "rutas_agregadas_totales",
        "rutas_unicas",
        "rutas_duplicadas",
        "prioridades",
        "relocks",
        "peso_base",
        "tope_unidades",
        "valor_mapeo",
        "unidades_aprobadas",
        "puntos_finales",
    ])
    for row in ranking:
        units = final_units_for_row(row, top_weight, max_units)
        writer.writerow([
            row["rank"],
            row["player"],
            row["discord_id"],
            row["road_total"],
            row["road_unique"],
            row["road_duplicates"],
            row["priority"],
            row["relock"],
            format_score(row["score"]),
            max_units,
            activity_value,
            units,
            units * activity_value,
        ])
    return io.BytesIO(output.getvalue().encode("utf-8"))


def duplicates_csv_bytes(duplicates):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["from", "to", "link_ids_repetidos", "jugadores", "message_ids"])
    for item in duplicates:
        writer.writerow([
            item["from"],
            item["to"],
            " | ".join(item["link_ids"]),
            " | ".join(item["players"]),
            " | ".join(item["message_ids"]),
        ])
    return io.BytesIO(output.getvalue().encode("utf-8"))


def events_csv_bytes(events):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "tipo_evento",
        "jugador",
        "discord_id",
        "fecha_aproximada",
        "from",
        "to",
        "mapa",
        "link_id",
        "message_id",
        "url",
    ])
    for event in events:
        writer.writerow([
            event.event_type,
            event.player,
            event.discord_id,
            event.created_at,
            event.from_map,
            event.to_map,
            event.map_name,
            event.link_id,
            event.source_message_id,
            event.source_url,
        ])
    return io.BytesIO(output.getvalue().encode("utf-8"))
