import os
import csv
import io
import asyncio
import traceback
import re
import unicodedata
import zipfile
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as xml_escape
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from database import (
    add_activity,
    add_evidence_participants,
    add_scout_alias,
    adjust_snapshot_activity,
    calc_puntos_totales,
    create_ranking_snapshot,
    create_evidence_review,
    get_evidence_by_thread,
    get_evidence_participants as db_get_evidence_participants,
    get_evidence_review_message_id,
    get_all_scouts,
    get_all_config,
    get_bot_state,
    get_latest_ranking_snapshot,
    get_puntos,
    get_nivel,
    get_pending_evidence_message_ids,
    get_scout_aliases,
    find_scout_alias,
    get_ranking_snapshot,
    get_ranking_snapshot_for_time,
    get_ranking_snapshot_rows,
    init_db,
    move_evidence_to_snapshot,
    remove_scout_alias,
    reset_all,
    set_bot_state,
    set_evidence_thread,
    set_evidence_review_message,
    set_puntos,
    subtract_activity,
)
from config import ACTIVIDADES, APPLICATION_ID, COLOR_PANEL, COLOR_RANKING, COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING, \
    DASHBOARD_CHANNEL_ID, GUILD_ID, INFO_RANKING_CHANNEL_ID, \
    WEEKLY_EXPORT_CHANNEL_ID, \
    EVIDENCE_CATEGORY, EVIDENCE_CATEGORY_ID, EVIDENCE_CATEGORY_IDS, EVIDENCE_CHANNEL_IDS, \
    EVIDENCE_CHANNELS, EVIDENCE_REVIEW_CHANNEL_ID, IMAGE_EXTENSIONS, LOG_CHANNEL_ID, \
    AUTO_RESET_ENABLED, AUTO_RESET_HOUR_UTC, AUTO_RESET_MINUTE_UTC, AUTO_RESET_WEEKDAY_UTC, \
    DEFAULT_PRIORITY_MIN_POINTS, PRIORITY_PROTECTED_ROLE_IDS, PRIORITY_ROLE_ID
from views import (
    DashboardView,
    EvidenceAuthorConfirmView,
    EvidenceThreadParticipantView,
    EvidenceReviewView,
    EvidenceReviewerSuggestionConfirmView,
    InfoRankingView,
    build_participant_resolution_embed,
    refresh_review_participants,
)
from embeds import build_dashboard_embed, build_info_ranking_embed, build_perfil_embed, build_priority_caps_embed
from permissions import can_review_member, is_admin, is_gm_member
from ocr import improve_confidence_for_channel, is_ineligible_ocr, read_message_ocr, suggest_activity_from_ocr
import participants as participant_tools
import mapping_analysis

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# ── Bot setup ─────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents, application_id=int(APPLICATION_ID))
tree = bot.tree
COMMANDS_SYNCED = False
RESET_TASK_STARTED = False
TAUNT_TASK_STARTED = False
AURA_TAUNT_RESPONSE = "RankingBot confirma: el aura se farmea, la envidia se nota. Sube evidencia o vuelve a zona azul."
AURA_TAUNT_TARGETS = (
    (1435778824775274581, 1514156352463700051),
)
MAPEO_LOG_CHANNEL_ID = 1505954990756204755
MAPEO_ANALYSIS_CHECKPOINT_KEY = "mapeo_analysis_checkpoint"
MAPEO_MAX_WEEKLY_UNITS = 25
MAPEO_ROAD_WEIGHT = 1.0
MAPEO_PRIORITY_WEIGHT = 0.15
MAPEO_RELOCK_WEIGHT = 0.15
MAPEO_SCALING_EXPONENT = 2.0
BOT_BUILD = "mapeo-range-v2"
DEFAULT_INACTIVE_MAX_POINTS = 0
INACTIVE_REPORT_LIMIT = 25

# Opciones de actividad para los slash commands
ACT_CHOICES = [
    app_commands.Choice(name=meta["label"], value=key)
    for key, meta in ACTIVIDADES.items()
]
RANKING_SOURCE_CHOICES = [
    app_commands.Choice(name="Ranking actual", value="actual"),
    app_commands.Choice(name="Ultimo cierre semanal", value="ultimo_cierre"),
]
EXPORT_FORMAT_CHOICES = [
    app_commands.Choice(name="Excel (.xlsx)", value="xlsx"),
    app_commands.Choice(name="CSV", value="csv"),
]
admin_group = app_commands.Group(name="admin", description="Herramientas de administracion del ranking")

async def send_interaction_error(interaction: discord.Interaction, message: str):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass


async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    original = getattr(error, "original", error)
    traceback.print_exception(type(original), original, original.__traceback__)
    await send_interaction_error(interaction, f"Ocurrio un error ejecutando el comando: `{original}`")


tree.on_error = on_app_command_error


class SafeView(discord.ui.View):
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        traceback.print_exception(type(error), error, error.__traceback__)
        await send_interaction_error(interaction, f"Ocurrio un error en el boton: `{error}`")


class SafeModal(discord.ui.Modal):
    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        traceback.print_exception(type(error), error, error.__traceback__)
        await send_interaction_error(interaction, f"Ocurrio un error en el formulario: `{error}`")

# ── Eventos ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    global COMMANDS_SYNCED, RESET_TASK_STARTED, TAUNT_TASK_STARTED
    init_db()
    bot.add_view(DashboardView())
    bot.add_view(InfoRankingView())
    for message_id in get_pending_evidence_message_ids():
        bot.add_view(EvidenceReviewView(message_id))
    if not COMMANDS_SYNCED:
        guild = discord.Object(id=GUILD_ID)
        commands_to_sync = list(tree.get_commands())
        tree.clear_commands(guild=None)
        await tree.sync()
        tree.clear_commands(guild=guild)
        for command in commands_to_sync:
            tree.add_command(command, guild=guild)
        synced = await tree.sync(guild=guild)
        COMMANDS_SYNCED = True
        print(f"✅ Bot listo: {bot.user} | Comandos sincronizados: {[cmd.name for cmd in synced]}")
    else:
        print(f"✅ Bot listo: {bot.user}")

    if AUTO_RESET_ENABLED and not RESET_TASK_STARTED:
        bot.loop.create_task(weekly_reset_loop())
        RESET_TASK_STARTED = True

    if not TAUNT_TASK_STARTED:
        bot.loop.create_task(reply_to_pending_aura_taunts())
        TAUNT_TASK_STARTED = True

# ── /panel_scouts ─────────────────────────────────────────────────────────────

@bot.event
async def on_message(message: discord.Message):
    if not message.guild:
        return

    if message.author.bot:
        return

    if should_reply_to_aura_taunt(message.content):
        try:
            await message.reply(AURA_TAUNT_RESPONSE, mention_author=True)
        except discord.HTTPException:
            pass

    if await handle_evidence_thread_message(message):
        return

    print(f"[MSG] guild={message.guild.id} channel={message.channel.id} category={message.channel.category_id} attachments={len(message.attachments)}")

    actividad = get_evidence_activity(message)
    if not actividad:
        print("[EVIDENCE] ignorado: canal/categoria no coincide")
        return

    if not has_image(message):
        print("[EVIDENCE] ignorado: sin imagen")
        return

    analyzing_embed = discord.Embed(
        title="Analizando evidencia",
        description="OCR en proceso. En breve se enviara a revision.",
        color=COLOR_WARNING
    )
    analyzing_msg = await message.reply(embed=analyzing_embed, mention_author=False)
    await set_pending_source_reaction(message)

    ocr_text = ""
    ocr_activity = None
    ocr_hits = []
    ocr_confidence = "Sin OCR"
    if has_image(message):
        try:
            ocr_text = await read_message_ocr(message)
            ocr_activity, ocr_hits, ocr_confidence = suggest_activity_from_ocr(ocr_text)
            if ocr_confidence == "Sin texto legible" or is_ineligible_ocr(ocr_text):
                ocr_text = ""
        except Exception as err:
            ocr_text = f"OCR error: {err}"
            print(f"[OCR ERROR] {err}")

    actividad, ocr_hits, ocr_confidence = improve_confidence_for_channel(actividad, ocr_activity, ocr_hits)

    participants, unresolved_names, suggested_participants = await get_evidence_participants(message)

    pts = create_evidence_review(
        str(message.id),
        str(message.author.id),
        message.author.display_name,
        actividad,
        participants
    )
    if pts <= 0:
        print("[EVIDENCE] duplicado")
        return

    review_channel = await get_review_channel(message)
    embed = discord.Embed(
        title="Evidencia pendiente",
        color=COLOR_WARNING,
        description=(
            f"Usuario: {message.author.mention}\n"
            f"Actividad: **{ACTIVIDADES[actividad]['label']}**\n"
            f"Confianza OCR: **{ocr_confidence}**\n"
            f"Puntos: `{pts}`\n"
            f"Canal: {message.channel.mention}\n"
            f"[Abrir evidencia]({message.jump_url})"
        )
    )
    if ocr_hits:
        embed.add_field(name="Detectado", value=", ".join(ocr_hits)[:1000], inline=False)
    if message.content.strip():
        embed.add_field(name="Texto", value=message.content[:1000], inline=False)
    if len(participants) > 1:
        participant_text = "\n".join(f"<@{user_id}>" for user_id, _ in participants[:20])
        embed.add_field(name="Participantes", value=participant_text[:1000], inline=False)
    if unresolved_names:
        embed.add_field(
            name="No resueltos",
            value=", ".join(f"`{name}`" for name in unresolved_names)[:1000],
            inline=False
        )
    if suggested_participants:
        embed.add_field(
            name="Sugerencias por confirmar",
            value=participant_tools.format_participant_suggestions(suggested_participants)[:1000],
            inline=False
        )
    image = first_image_url(message)
    if image:
        embed.set_image(url=image)

    review_msg = await review_channel.send(
        embed=embed,
        view=EvidenceReviewView(str(message.id))
    )
    set_evidence_review_message(str(message.id), str(review_msg.id))
    print(f"[EVIDENCE] enviado review={review_msg.id}")

    participant_thread = None
    if should_create_participant_thread(actividad, message.content, suggested_participants, unresolved_names):
        participant_thread = await create_participant_thread(
            message,
            actividad,
            review_msg,
            suggested_participants,
            unresolved_names,
        )

    if not participant_thread and (suggested_participants or unresolved_names):
        author_confirm_view = None
        if suggested_participants:
            author_confirm_view = EvidenceAuthorConfirmView(
                str(message.id),
                str(message.author.id),
                suggested_participants,
                review_msg,
            )
        await message.reply(
            embed=build_participant_confirmation_embed(suggested_participants, unresolved_names),
            view=author_confirm_view,
            mention_author=True,
        )

    done_description = f"Revision: {review_msg.jump_url}"
    if participant_thread:
        done_description += f"\nHilo de participantes: {participant_thread.mention}"
    done_embed = discord.Embed(
        title="Evidencia enviada a revision",
        description=done_description,
        color=COLOR_SUCCESS
    )
    await analyzing_msg.edit(embed=done_embed)
    await asyncio.sleep(60)
    await analyzing_msg.delete()


# ── Bromas / respuestas automáticas ───────────────────────────────────────────

async def reply_to_pending_aura_taunts():
    for channel_id, message_id in AURA_TAUNT_TARGETS:
        state_key = f"aura_taunt_reply:{message_id}"
        if get_bot_state(state_key):
            continue

        try:
            channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
            message = await channel.fetch_message(message_id)
            await message.reply(AURA_TAUNT_RESPONSE, mention_author=True)
            set_bot_state(state_key, datetime.now(timezone.utc).isoformat())
            print(f"[TAUNT] Respuesta enviada a mensaje {message_id}")
        except discord.NotFound:
            print(f"[TAUNT] Mensaje no encontrado: {message_id}")
        except discord.Forbidden:
            print(f"[TAUNT] Sin permisos para responder en canal {channel_id}")
        except discord.HTTPException as err:
            print(f"[TAUNT ERROR] {message_id}: {err}")


def should_reply_to_aura_taunt(text: str):
    normalized = re.sub(r"\s+", " ", (text or "").lower()).strip()
    return "aura bot" in normalized or "farmeaste aura" in normalized


# ── Evidencias ────────────────────────────────────────────────────────────────

def get_evidence_activity(message: discord.Message):
    if message.channel.id in EVIDENCE_CHANNEL_IDS:
        return EVIDENCE_CHANNEL_IDS[message.channel.id]

    category = message.channel.category
    if EVIDENCE_CATEGORY_IDS and (not category or category.id not in EVIDENCE_CATEGORY_IDS):
        return None
    if not EVIDENCE_CATEGORY_IDS and EVIDENCE_CATEGORY_ID and (not category or category.id != EVIDENCE_CATEGORY_ID):
        return None
    if not EVIDENCE_CATEGORY_IDS and not EVIDENCE_CATEGORY_ID and (not category or category.name.lower() != EVIDENCE_CATEGORY):
        return None

    channel_name = clean_channel_name(message.channel.name)
    for key, actividad in EVIDENCE_CHANNELS.items():
        if key in channel_name:
            return actividad
    return activity_from_text(message.content)

def clean_channel_name(name: str):
    return "".join(ch.lower() if ch.isalnum() else " " for ch in name)

def activity_from_text(text: str):
    text = text.lower()
    for key, actividad in EVIDENCE_CHANNELS.items():
        if key in text:
            return actividad
    return None

def has_image(message: discord.Message):
    for attachment in message.attachments:
        if is_supported_image(attachment):
            return True
    return False

def first_image_url(message: discord.Message):
    for attachment in message.attachments:
        if is_supported_image(attachment):
            return attachment.url
    return None

def is_supported_image(attachment: discord.Attachment):
    content_type = attachment.content_type or ""
    filename = attachment.filename.lower()
    if content_type == "image/gif" or filename.endswith(".gif"):
        return False
    return content_type.startswith("image/") or filename.endswith(IMAGE_EXTENSIONS)

async def set_pending_source_reaction(message: discord.Message):
    for emoji in (
        "\N{WHITE HEAVY CHECK MARK}",
        "\N{CROSS MARK}",
        "\N{OUTBOX TRAY}",
        "\N{HOURGLASS}",
    ):
        await remove_bot_reaction(message, emoji)
    try:
        await message.add_reaction("\N{HOURGLASS}")
    except discord.HTTPException:
        pass

async def remove_bot_reaction(message: discord.Message, emoji: str):
    bot_user = message.guild.me if message.guild else bot.user
    if not bot_user:
        return
    try:
        await message.remove_reaction(emoji, bot_user)
    except discord.HTTPException:
        pass

async def get_evidence_participants(message: discord.Message):
    participants = {str(message.author.id): message.author.display_name}

    for member in message.mentions:
        if not member.bot:
            participants[str(member.id)] = member.display_name

    resolved, unresolved, suggestions = await participant_tools.resolve_plus_names(
        message.guild,
        message.content,
        set(participants),
    )
    for user_id, display_name in resolved:
        participants[str(user_id)] = display_name

    return list(participants.items()), unresolved, suggestions

SUMMARY_NAME_RE = re.compile(r"[A-Za-z0-9_.-]{2,40}")
SUMMARY_TIME_RE = re.compile(r"(?:(\d{1,3})\s*h)?\s*(\d{1,2})\s*m\b", re.IGNORECASE)
SUMMARY_MAPS_RE = re.compile(r"(\d{1,3})\s*(?:mapas?|maps?)\b", re.IGNORECASE)
SUMMARY_DATE_RE = re.compile(r"\b(\d{1,2})\s+de\s+([a-z]+)\s+de\s+(\d{4})\b", re.IGNORECASE)
SPANISH_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
SCOUTEO_HOURS_PER_POINT = 5
SCOUTEO_MAPS_PER_POINT = 3
SCOUTEO_HOURS_SETTING_KEY = "scouteo_count_hours_per_point"
SCOUTEO_MAPS_SETTING_KEY = "scouteo_count_maps_per_point"
SCOUTEO_DASHBOARD_STATE_PREFIX = "scouteo_count_dashboard:"

def get_scouteo_count_settings():
    return (
        int(get_bot_state(SCOUTEO_HOURS_SETTING_KEY) or SCOUTEO_HOURS_PER_POINT),
        int(get_bot_state(SCOUTEO_MAPS_SETTING_KEY) or SCOUTEO_MAPS_PER_POINT),
    )

def set_scouteo_count_settings(hours_per_point: int, maps_per_point: int):
    set_bot_state(SCOUTEO_HOURS_SETTING_KEY, str(max(1, int(hours_per_point))))
    set_bot_state(SCOUTEO_MAPS_SETTING_KEY, str(max(1, int(maps_per_point))))

def get_embed_search_text(message: discord.Message):
    parts = [message.content or ""]
    for embed in message.embeds:
        parts.extend([
            embed.title or "",
            embed.description or "",
            getattr(embed.footer, "text", "") or "",
            getattr(embed.author, "name", "") or "",
        ])
        for field in embed.fields:
            parts.append(field.name or "")
            parts.append(field.value or "")
    return "\n".join(part for part in parts if part)

def normalize_search_text(text: str):
    normalized = unicodedata.normalize("NFD", text or "")
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn").lower()

def extract_scouteo_summary_date(message: discord.Message):
    text = normalize_search_text(get_embed_search_text(message))
    match = SUMMARY_DATE_RE.search(text)
    if not match:
        return None

    day = int(match.group(1))
    month = SPANISH_MONTHS.get(match.group(2).lower())
    year = int(match.group(3))
    if not month:
        return None

    try:
        return datetime(year, month, day, 12, 0, tzinfo=timezone.utc)
    except ValueError:
        return None

def get_scouteo_count_target(message: discord.Message, source: str = "auto"):
    source = str(source or "auto").strip().lower().replace("-", "_")
    if source in {"actual", "ranking_actual"}:
        return {
            "snapshot_id": None,
            "label": "Ranking actual",
            "late": False,
            "missing": False,
            "summary_at": None,
        }

    if source in {"ultimo", "ultimo_cierre", "cierre"}:
        snapshot = get_latest_ranking_snapshot()
        if snapshot:
            return {
                "snapshot_id": int(snapshot[0]),
                "label": f"Cierre semanal #{snapshot[0]} ({snapshot[3]})",
                "late": True,
                "missing": False,
                "summary_at": None,
            }
        return {
            "snapshot_id": None,
            "label": "Ultimo cierre semanal no encontrado",
            "late": True,
            "missing": True,
            "summary_at": None,
        }

    summary_at = extract_scouteo_summary_date(message)
    if not summary_at:
        summary_at = message.created_at
        if summary_at.tzinfo is None:
            summary_at = summary_at.replace(tzinfo=timezone.utc)
        else:
            summary_at = summary_at.astimezone(timezone.utc)

    if summary_at >= current_weekly_ranking_start():
        return {
            "snapshot_id": None,
            "label": "Ranking actual",
            "late": False,
            "missing": False,
            "summary_at": summary_at,
        }

    snapshot = get_ranking_snapshot_for_time(summary_at)
    if snapshot:
        return {
            "snapshot_id": int(snapshot[0]),
            "label": f"Cierre semanal #{snapshot[0]} ({snapshot[3]})",
            "late": True,
            "missing": False,
            "summary_at": summary_at,
        }

    return {
        "snapshot_id": None,
        "label": "Semana cerrada sin archivo guardado",
        "late": True,
        "missing": True,
        "summary_at": summary_at,
    }

def extract_scouteo_summary_records(message: discord.Message):
    text = get_embed_search_text(message)
    if not text:
        return []

    normalized = normalize_search_text(text)
    if "resumen del dia" not in normalized:
        return []
    if "scout" not in normalized or "mapas" not in normalized:
        return []

    records = []
    seen = set()
    for line in text.splitlines():
        if "mapas" not in line.lower():
            continue

        time_match = SUMMARY_TIME_RE.search(line)
        maps_match = SUMMARY_MAPS_RE.search(line)
        if not time_match or not maps_match:
            continue

        name = extract_scouteo_summary_name(line[:time_match.start()])
        if not name:
            continue

        key = participant_tools.normalize_name(name)
        if not key or key in seen:
            continue
        seen.add(key)

        hours = int(time_match.group(1) or 0)
        minutes = int(time_match.group(2))
        maps = int(maps_match.group(1))
        records.append({
            "name": name,
            "hours": hours,
            "minutes": minutes,
            "maps": maps,
        })
    return records

def calculate_scouteo_records(records: list[dict], hours_per_point: int, maps_per_point: int):
    calculated = []
    for record in records:
        item = dict(record)
        hour_points = ((item["hours"] * 60) + item["minutes"]) // (hours_per_point * 60)
        map_points = item["maps"] // maps_per_point
        item["hour_points"] = hour_points
        item["map_points"] = map_points
        item["total"] = hour_points + map_points
        calculated.append(item)
    return calculated

def combine_scouteo_records(records: list[dict]):
    if not records:
        return {
            "name": "",
            "hours": 0,
            "minutes": 0,
            "maps": 0,
            "hour_points": 0,
            "map_points": 0,
            "total": 0,
            "source_names": [],
        }

    total_minutes = sum((record["hours"] * 60) + record["minutes"] for record in records)
    source_names = [record["name"] for record in records]
    return {
        "name": records[0]["name"],
        "hours": total_minutes // 60,
        "minutes": total_minutes % 60,
        "maps": sum(record["maps"] for record in records),
        "hour_points": sum(record["hour_points"] for record in records),
        "map_points": sum(record["map_points"] for record in records),
        "total": sum(record["total"] for record in records),
        "source_names": source_names,
    }

def format_unresolved_scouteo_records(records: list[dict], unit_points: int):
    lines = []
    for record in records[:20]:
        lines.append(
            f"`{record['name']}` - `{record['total'] * unit_points}` pts "
            f"({record['hours']}h {record['minutes']}m, {record['maps']} mapas)"
        )
    if len(records) > 20:
        lines.append(f"... y {len(records) - 20} mas")
    return "\n".join(lines)

def format_scouteo_participant_line(user_id: str, cantidad: int, record: dict, unit_points: int):
    source_names = record.get("source_names") or [record["name"]]
    source_text = ""
    if len(source_names) > 1:
        source_text = f"; nombres: {', '.join(source_names[:4])}"
        if len(source_names) > 4:
            source_text += f" +{len(source_names) - 4}"

    return (
        f"<@{user_id}> - `{cantidad * unit_points}` pts "
        f"({record['hours']}h {record['minutes']}m, {record['maps']} mapas; "
        f"{record['hour_points']} por horas + {record['map_points']} por mapas"
        f"{source_text})"
    )

def extract_scouteo_summary_name(text: str):
    candidates = [
        token.strip("._-")
        for token in SUMMARY_NAME_RE.findall(text or "")
        if not token.isdigit()
    ]
    return candidates[-1] if candidates else None

async def handle_scouteo_summary_message(message: discord.Message):
    if get_evidence_activity(message) != "scouteo":
        return False

    records = extract_scouteo_summary_records(message)
    if not records:
        return False

    target = get_scouteo_count_target(message)
    if target["missing"]:
        print("[SCOUTEO SUMMARY] ignorado: resumen anterior al reset sin cierre guardado")
        return False

    hours_per_point, maps_per_point = get_scouteo_count_settings()
    records = calculate_scouteo_records(records, hours_per_point, maps_per_point)
    return await create_scouteo_count_review(
        message,
        records,
        hours_per_point,
        maps_per_point,
        target["snapshot_id"],
        target["label"],
    )

async def create_scouteo_count_review(
    message: discord.Message,
    records: list[dict],
    hours_per_point: int,
    maps_per_point: int,
    target_snapshot_id: int | None = None,
    target_label: str = "Ranking actual",
):
    participant_rows_by_user = {}
    unresolved_records = []
    suggested_participants = []
    for record in records:
        if record["total"] <= 0:
            continue
        participants, unresolved, suggestions = await participant_tools.resolve_names(
            message.guild,
            [record["name"]],
        )
        if participants:
            user_id, display_name = participants[0]
            user_id = str(user_id)
            if user_id not in participant_rows_by_user:
                participant_rows_by_user[user_id] = {
                    "user_id": user_id,
                    "display_name": display_name,
                    "cantidad": 0,
                    "records": [],
                }
            participant_rows_by_user[user_id]["display_name"] = display_name
            participant_rows_by_user[user_id]["cantidad"] += record["total"]
            participant_rows_by_user[user_id]["records"].append(record)
        else:
            unresolved_records.extend(dict(record, name=name) for name in unresolved)
            suggested_participants.extend(suggestions)

    participant_rows = [
        (
            item["user_id"],
            item["display_name"],
            item["cantidad"],
            combine_scouteo_records(item["records"]),
        )
        for item in participant_rows_by_user.values()
    ]
    unresolved_names = [record["name"] for record in unresolved_records]

    if not participant_rows:
        print("[SCOUTEO SUMMARY] ignorado: sin participantes con puntos calculados")
        return False

    owner_id, owner_name, _, _ = participant_rows[0]
    pts = create_evidence_review(
        str(message.id),
        owner_id,
        owner_name,
        "scouteo",
        participant_rows,
        target_snapshot_id=target_snapshot_id,
    )
    if pts <= 0:
        print("[SCOUTEO SUMMARY] duplicado")
        return False

    review_channel = await get_review_channel(message)
    embed = discord.Embed(
        title="Evidencia pendiente",
        color=COLOR_WARNING,
        description=(
            f"Usuario: {message.author.mention}\n"
            f"Actividad: **{ACTIVIDADES['scouteo']['label']}**\n"
            f"Origen: **Resumen del Dia**\n"
            f"Destino: **{target_label}**\n"
            f"Reglas: `{hours_per_point}h = 1 punto | {maps_per_point} mapas = 1 punto`\n"
            f"Total calculado: `{sum(row[2] for row in participant_rows) * pts}` pts\n"
            f"Canal: {message.channel.mention}\n"
            f"[Abrir evidencia]({message.jump_url})"
        )
    )
    participant_text = "\n".join(
        format_scouteo_participant_line(user_id, cantidad, record, pts)
        for user_id, _, cantidad, record in participant_rows[:20]
    )
    embed.add_field(name="Participantes", value=participant_text[:1000], inline=False)
    if unresolved_records:
        embed.add_field(
            name="No resueltos",
            value=format_unresolved_scouteo_records(unresolved_records, pts)[:1000],
            inline=False
        )
    if suggested_participants:
        embed.add_field(
            name="Sugerencias por confirmar",
            value=participant_tools.format_participant_suggestions(suggested_participants)[:1000],
            inline=False
        )

    review_msg = await review_channel.send(
        embed=embed,
        view=EvidenceReviewView(str(message.id))
    )
    set_evidence_review_message(str(message.id), str(review_msg.id))
    await set_pending_source_reaction(message)
    print(f"[SCOUTEO SUMMARY] enviado review={review_msg.id}")

    if suggested_participants or unresolved_names:
        await message.reply(
            embed=build_participant_confirmation_embed(suggested_participants, unresolved_names),
            mention_author=False,
        )

    return True

def build_participant_confirmation_embed(suggestions: list[dict], unresolved_names: list[str]):
    has_suggestions = bool(suggestions)
    has_unresolved = bool(unresolved_names)
    if not has_suggestions and not has_unresolved:
        title = "Agregar participantes"
        description = "Usa este hilo para agregar personas relacionadas a la evidencia."
    elif has_suggestions:
        title = "Confirmar participantes"
        description = (
            "Reconoci algunos nombres escritos en la evidencia. "
            "Marca solo las personas correctas y confirma para agregarlas a la evidencia."
        )
    else:
        title = "Participantes sin reconocer"
        description = (
            "No pude asociar estos nombres a una cuenta. "
            "Corrigelos en este hilo con menciones, IDs, `+nombres` o nombres separados por espacios."
        )

    embed = discord.Embed(
        title=title,
        description=description,
        color=COLOR_WARNING,
    )
    if suggestions:
        embed.add_field(
            name="Sugerencias",
            value=participant_tools.format_participant_suggestions(suggestions)[:1000],
            inline=False,
        )
    if unresolved_names:
        embed.add_field(
            name="Sin coincidencia",
            value=", ".join(f"`{name}`" for name in unresolved_names)[:1000],
            inline=False,
        )
    return embed

def should_create_participant_thread(
    actividad: str,
    content: str,
    suggestions: list[dict],
    unresolved_names: list[str],
):
    return (
        bool(suggestions)
        or bool(unresolved_names)
        or actividad == "limpieza_aspecto"
        or len(participant_tools.extract_plus_names(content)) >= 3
    )

async def create_participant_thread(
    message: discord.Message,
    actividad: str,
    review_msg: discord.Message,
    suggestions: list[dict],
    unresolved_names: list[str],
):
    try:
        thread_name = build_participant_thread_name(message, actividad)
        thread = await message.create_thread(name=thread_name, auto_archive_duration=1440)
        set_evidence_thread(str(message.id), str(thread.id))
        await add_participant_thread_to_review(review_msg, thread)

        embed = build_participant_confirmation_embed(suggestions, unresolved_names)
        embed.add_field(
            name="Como corregir",
            value=(
                "Escribe aqui menciones, IDs, `+nombres`, comas, saltos de linea o nombres separados por espacios. "
                "Ejemplo: `violeth chino littleponny`."
            ),
            inline=False,
        )

        view = EvidenceThreadParticipantView(str(message.id), review_msg)
        if suggestions:
            view = EvidenceAuthorConfirmView(
                str(message.id),
                str(message.author.id),
                suggestions,
                review_msg,
            )

        await thread.send(
            content=f"{message.author.mention} hilo para corregir participantes de esta evidencia.",
            embed=embed,
            view=view,
        )
        return thread
    except discord.Forbidden:
        print("[THREAD] Sin permisos para crear hilo de participantes")
    except discord.HTTPException as err:
        print(f"[THREAD ERROR] No se pudo crear hilo: {err}")
    return None

async def add_participant_thread_to_review(review_msg: discord.Message, thread: discord.Thread):
    if not review_msg.embeds:
        return
    embed = review_msg.embeds[0]
    for index, field in enumerate(embed.fields):
        if field.name == "Hilo participantes":
            embed.set_field_at(index, name=field.name, value=thread.mention, inline=False)
            await review_msg.edit(embed=embed)
            return
    embed.add_field(name="Hilo participantes", value=thread.mention, inline=False)
    await review_msg.edit(embed=embed)

def build_participant_thread_name(message: discord.Message, actividad: str):
    label = ACTIVIDADES.get(actividad, {}).get("label", "Evidencia")
    base = f"Participantes {label} - {message.author.display_name}"
    return base[:90]

async def handle_evidence_thread_message(message: discord.Message):
    if not isinstance(message.channel, discord.Thread):
        return False

    evidence = get_evidence_by_thread(str(message.channel.id))
    if not evidence:
        return False

    evidence_message_id, author_id, _, status, review_message_id = evidence
    if not participant_tools.contains_participant_reference(message.content):
        return True

    if str(message.author.id) != str(author_id) and not can_review_member(message.author):
        await message.reply(
            "Solo el autor de la evidencia o un revisor puede agregar participantes aqui.",
            mention_author=False,
        )
        return True

    if status != "pending":
        await message.reply("Esta evidencia ya fue revisada.", mention_author=False)
        return True

    review_message = await fetch_review_message(review_message_id or get_evidence_review_message_id(evidence_message_id))
    existing_user_ids = {user_id for user_id, _ in db_get_evidence_participants(evidence_message_id)}
    participants, unresolved, suggestions = await participant_tools.resolve_manual_names(
        message.guild,
        message.content,
        existing_user_ids,
    )

    added = []
    if participants:
        if not add_evidence_participants(evidence_message_id, participants):
            await message.reply("Esta evidencia ya fue revisada.", mention_author=False)
            return True
        added = participants
        if review_message:
            await refresh_review_participants(review_message, evidence_message_id)

    embed = build_participant_resolution_embed(added, suggestions, unresolved)
    view = None
    if suggestions and review_message:
        if can_review_member(message.author):
            view = EvidenceReviewerSuggestionConfirmView(evidence_message_id, review_message, suggestions)
        else:
            view = EvidenceAuthorConfirmView(evidence_message_id, author_id, suggestions, review_message)

    await message.reply(embed=embed, view=view, mention_author=False)
    return True

async def fetch_review_message(review_message_id: str | None):
    if not review_message_id:
        return None
    try:
        channel = bot.get_channel(EVIDENCE_REVIEW_CHANNEL_ID) or await bot.fetch_channel(EVIDENCE_REVIEW_CHANNEL_ID)
        return await channel.fetch_message(int(review_message_id))
    except (discord.HTTPException, ValueError, TypeError, AttributeError):
        return None

async def get_review_channel(message: discord.Message):
    if EVIDENCE_REVIEW_CHANNEL_ID:
        channel = bot.get_channel(EVIDENCE_REVIEW_CHANNEL_ID)
        if channel:
            return channel
        try:
            return await bot.fetch_channel(EVIDENCE_REVIEW_CHANNEL_ID)
        except discord.HTTPException:
            pass
    return message.channel


@admin_group.command(name="conteo", description="Calcula scouteo desde un resumen diario por ID de mensaje")
@app_commands.describe(
    id_mensaje="ID del mensaje del resumen de Mapas en este canal",
    fuente="Auto detecta por fecha, o fuerza ranking/cierre",
)
@app_commands.choices(fuente=[
    app_commands.Choice(name="Auto detectar", value="auto"),
    app_commands.Choice(name="Ranking actual", value="actual"),
    app_commands.Choice(name="Ultimo cierre semanal", value="ultimo_cierre"),
])
async def conteo(interaction: discord.Interaction, id_mensaje: str, fuente: str = "auto"):
    if not can_review_member(interaction.user):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return

    try:
        message_id = int(id_mensaje.strip())
    except ValueError:
        await interaction.response.send_message("Ese ID de mensaje no es valido.", ephemeral=True)
        return

    try:
        source_message = await interaction.channel.fetch_message(message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError):
        await interaction.response.send_message(
            "No pude encontrar ese mensaje en este canal. Usa el ID del resumen y ejecuta el comando en el mismo canal.",
            ephemeral=True,
        )
        return

    if get_evidence_activity(source_message) != "scouteo":
        await interaction.response.send_message("Ese mensaje no esta en un canal configurado como scouteo.", ephemeral=True)
        return

    records = extract_scouteo_summary_records(source_message)
    if not records:
        await interaction.response.send_message("No pude leer nombres, horas y mapas en ese resumen.", ephemeral=True)
        return

    target = get_scouteo_count_target(source_message, fuente)
    if target["missing"]:
        await interaction.response.send_message(
            "Ese resumen parece ser de una semana ya cerrada, pero no encontre un cierre semanal guardado para sumarlo.",
            ephemeral=True,
        )
        return

    hours_per_point, maps_per_point = get_scouteo_count_settings()
    embed = build_scouteo_count_embed(
        source_message,
        calculate_scouteo_records(records, hours_per_point, maps_per_point),
        hours_per_point,
        maps_per_point,
        target["label"],
        target["late"],
    )
    await interaction.response.send_message(
        embed=embed,
        view=ScouteoCountView(
            source_message,
            records,
            hours_per_point,
            maps_per_point,
            target["snapshot_id"],
            target["label"],
            target["late"],
        ),
    )


@admin_group.command(name="mover_conteo_cierre", description="Mueve un conteo aprobado del ranking actual al cierre semanal")
@app_commands.describe(
    id_mensaje="ID del mensaje/resumen que ya fue contado",
    cierre_id="Opcional: ID del cierre. Si lo dejas vacio usa el ultimo cierre.",
)
async def mover_conteo_cierre(
    interaction: discord.Interaction,
    id_mensaje: str,
    cierre_id: int | None = None,
):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return

    message_id = str(id_mensaje).strip()
    if not message_id.isdigit():
        await interaction.response.send_message("Ese ID de mensaje no es valido.", ephemeral=True)
        return

    snapshot = get_ranking_snapshot(cierre_id) if cierre_id else get_latest_ranking_snapshot()
    if not snapshot:
        await interaction.response.send_message("No encontre un cierre semanal guardado para mover ese conteo.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    result = move_evidence_to_snapshot(message_id, int(snapshot[0]))
    if not result.get("ok"):
        embed = build_move_count_error_embed(message_id, result)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    if result["status"] == "approved_moved":
        await publish_or_update_dashboard()
        await publish_or_update_info_ranking()

    embed = build_move_count_result_embed(message_id, snapshot, result)
    await interaction.followup.send(embed=embed, ephemeral=True)


def build_move_count_error_embed(message_id: str, result: dict):
    reason = result.get("reason")
    messages = {
        "snapshot_not_found": "No encontre ese cierre semanal.",
        "evidence_not_found": "No encontre una evidencia/conteo con ese ID.",
        "already_snapshot": f"Ese conteo ya esta asociado al cierre `#{result.get('snapshot_id')}`.",
        "rejected": "Ese conteo fue rechazado, asi que no hay puntos que mover.",
        "invalid_activity": f"Esa actividad no se puede mover automaticamente: `{result.get('activity')}`.",
    }
    embed = discord.Embed(
        title="No pude mover el conteo",
        description=messages.get(reason, f"Estado no soportado: `{reason or result.get('status')}`"),
        color=COLOR_ERROR,
    )
    embed.add_field(name="ID mensaje", value=f"`{message_id}`", inline=False)
    return embed


def build_move_count_result_embed(message_id: str, snapshot, result: dict):
    if result["status"] == "pending_retargeted":
        embed = discord.Embed(
            title="Conteo redirigido al cierre",
            description=(
                f"El conteo aun estaba pendiente, asi que ahora al aprobarse ira al cierre `#{snapshot[0]}`.\n"
                f"ID mensaje: `{message_id}`"
            ),
            color=COLOR_SUCCESS,
        )
        return embed

    participants = result.get("participants", [])
    lines = [
        (
            f"<@{item['user_id']}> - `{item['units']}` unidades "
            f"(`{item['points']}` pts)"
        )
        for item in participants[:15]
    ]
    if len(participants) > 15:
        lines.append(f"... y {len(participants) - 15} mas")

    partial = [
        item for item in participants
        if item.get("removed_units", item["units"]) < item["units"]
    ]
    embed = discord.Embed(
        title="Conteo movido al cierre semanal",
        description=(
            f"ID mensaje: `{message_id}`\n"
            f"Destino: **Cierre semanal #{snapshot[0]} ({snapshot[3]})**\n"
            f"Actividad: `{result['activity']}`\n"
            f"Total movido: `{result['units']}` unidades = `{result['points']}` pts"
        ),
        color=COLOR_SUCCESS,
    )
    embed.add_field(name="Participantes", value="\n".join(lines) if lines else "Sin participantes.", inline=False)
    if partial:
        embed.add_field(
            name="Ojo",
            value=(
                "A algunos usuarios se les resto menos del ranking actual porque ya no tenian todas esas unidades ahi. "
                "El cierre recibio el conteo completo."
            ),
            inline=False,
        )
    return embed


def build_scouteo_count_embed(
    source_message: discord.Message,
    records: list[dict],
    hours_per_point: int,
    maps_per_point: int,
    target_label: str = "Ranking actual",
    is_late_closure: bool = False,
):
    unit_points = get_puntos("scouteo")
    total_units = sum(record["total"] for record in records)
    embed = discord.Embed(
        title="Conteo de scouteo",
        description=(
            f"Mensaje: [abrir resumen]({source_message.jump_url})\n"
            f"Destino: **{target_label}**\n"
            f"Reglas: `{hours_per_point}h = 1 punto | {maps_per_point} mapas = 1 punto`\n"
            f"Valor Scouteo: `{unit_points}` pts por unidad\n"
            f"Total: `{total_units * unit_points}` pts"
        ),
        color=COLOR_WARNING,
    )
    embed.add_field(
        name="Preview",
        value=format_scouteo_count_table(records, unit_points),
        inline=False,
    )
    if is_late_closure:
        embed.add_field(
            name="Criterio semanal",
            value="La fecha del resumen pertenece a la semana cerrada; al aprobarlo no suma al ranking nuevo.",
            inline=False,
        )
    return embed


def format_scouteo_count_table(records: list[dict], unit_points: int):
    if not records:
        return "Sin puntos calculados."

    lines = [
        "Scout        Tiempo  Map Hrs Mps Ud Pts",
        "------------ ------- --- --- --- -- ---",
    ]
    for record in records[:12]:
        name = record["name"][:12].ljust(12)
        time_text = f"{record['hours']}h{record['minutes']:02d}m".rjust(7)
        maps = str(record["maps"]).rjust(3)
        hour_points = str(record["hour_points"]).rjust(3)
        map_points = str(record["map_points"]).rjust(3)
        total = str(record["total"]).rjust(2)
        points = str(record["total"] * unit_points).rjust(3)
        lines.append(f"{name} {time_text} {maps} {hour_points} {map_points} {total} {points}")

    if len(records) > 12:
        lines.append(f"... y {len(records) - 12} mas")

    return f"```text\n{chr(10).join(lines)}\n```"


class ScouteoCountView(SafeView):
    def __init__(
        self,
        source_message: discord.Message,
        records: list[dict],
        hours_per_point: int,
        maps_per_point: int,
        target_snapshot_id: int | None = None,
        target_label: str = "Ranking actual",
        is_late_closure: bool = False,
    ):
        super().__init__(timeout=300)
        self.source_message = source_message
        self.records = records
        self.hours_per_point = hours_per_point
        self.maps_per_point = maps_per_point
        self.target_snapshot_id = target_snapshot_id
        self.target_label = target_label
        self.is_late_closure = is_late_closure

    def calculated_records(self):
        return calculate_scouteo_records(self.records, self.hours_per_point, self.maps_per_point)

    async def refresh(self, interaction: discord.Interaction):
        embed = build_scouteo_count_embed(
            self.source_message,
            self.calculated_records(),
            self.hours_per_point,
            self.maps_per_point,
            self.target_label,
            self.is_late_closure,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Horas", style=discord.ButtonStyle.secondary)
    async def change_hours(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_review_member(interaction.user):
            await interaction.response.send_message("No tienes permiso para editar este conteo.", ephemeral=True)
            return
        await interaction.response.send_modal(ScouteoCountRuleModal(self, "hours"))

    @discord.ui.button(label="Mapas", style=discord.ButtonStyle.secondary)
    async def change_maps(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_review_member(interaction.user):
            await interaction.response.send_message("No tienes permiso para editar este conteo.", ephemeral=True)
            return
        await interaction.response.send_modal(ScouteoCountRuleModal(self, "maps"))

    @discord.ui.button(label="Enviar a revision", style=discord.ButtonStyle.success)
    async def send_review(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_review_member(interaction.user):
            await interaction.response.send_message("No tienes permiso para enviar este conteo.", ephemeral=True)
            return
        set_scouteo_count_settings(self.hours_per_point, self.maps_per_point)
        created = await create_scouteo_count_review(
            self.source_message,
            self.calculated_records(),
            self.hours_per_point,
            self.maps_per_point,
            self.target_snapshot_id,
            self.target_label,
        )
        if not created:
            await interaction.response.send_message("No se pudo crear la revision. Puede que ya exista o no haya puntos.", ephemeral=True)
            return

        for item in self.children:
            item.disabled = True
        set_bot_state(
            f"{SCOUTEO_DASHBOARD_STATE_PREFIX}{self.source_message.id}",
            f"{interaction.channel.id}:{interaction.message.id}",
        )
        await interaction.response.edit_message(
            content="Conteo enviado a revision.",
            embed=build_scouteo_count_embed(
                self.source_message,
                self.calculated_records(),
                self.hours_per_point,
                self.maps_per_point,
                self.target_label,
                self.is_late_closure,
            ),
            view=self,
        )


class ScouteoCountRuleModal(SafeModal):
    value = discord.ui.TextInput(label="Valor", placeholder="Ej: 5", max_length=3)

    def __init__(self, view: ScouteoCountView, target: str):
        title = "Horas para 1 punto" if target == "hours" else "Mapas para 1 punto"
        super().__init__(title=title)
        self.view_ref = view
        self.target = target
        current = view.hours_per_point if target == "hours" else view.maps_per_point
        self.value.default = str(current)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(str(self.value.value).strip())
            if amount <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Ingresa un numero mayor a 0.", ephemeral=True)
            return

        if self.target == "hours":
            self.view_ref.hours_per_point = amount
        else:
            self.view_ref.maps_per_point = amount
        await self.view_ref.refresh(interaction)


@admin_group.command(name="analizar_mapeo", description="Analiza logs de mapeo desde el inicio semanal del ranking")
@app_commands.describe(
    fuente="Destino del conteo: ranking actual o ultimo cierre semanal",
)
@app_commands.choices(fuente=[
    app_commands.Choice(name="Ranking actual", value="actual"),
    app_commands.Choice(name="Ultimo cierre semanal", value="ultimo_cierre"),
])
async def analizar_mapeo(interaction: discord.Interaction, fuente: str = "actual"):
    if not can_review_member(interaction.user):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    channel = interaction.client.get_channel(MAPEO_LOG_CHANNEL_ID)
    if not channel:
        try:
            channel = await interaction.client.fetch_channel(MAPEO_LOG_CHANNEL_ID)
        except discord.HTTPException:
            await interaction.followup.send("No pude abrir el canal de mapeo configurado.")
            return

    target = get_mapeo_count_target(fuente)
    if target["missing"]:
        await interaction.followup.send("No encontre un cierre semanal guardado para sumar el mapeo.", ephemeral=True)
        return

    analysis_start = target["analysis_start"]
    analysis_end = target["analysis_end"]
    events = []
    scanned = 0
    latest_event_at = None
    async for message in channel.history(limit=None, after=analysis_start, before=analysis_end):
        scanned += 1
        event = mapping_analysis.parse_mapping_message(message)
        if event:
            await enrich_mapping_event_player(message.guild, event)
            events.append(event)
            if not latest_event_at or message.created_at > latest_event_at:
                latest_event_at = message.created_at

    if not events:
        range_end = f" hasta `{analysis_end.strftime('%Y-%m-%d %H:%M UTC')}`" if analysis_end else ""
        fallback_text = " Rango corregido automaticamente a 7 dias." if target.get("range_fallback") else ""
        await interaction.followup.send(
            f"No encontre eventos validos de mapeo desde `{analysis_start.strftime('%Y-%m-%d %H:%M UTC')}`{range_end}."
            f"{fallback_text} Build `{BOT_BUILD}`."
        )
        return

    analysis = mapping_analysis.analyze_mapping_events(events)
    await interaction.followup.send(
        embed=build_mapeo_analysis_embed(
            analysis,
            scanned,
            analysis_start,
            MAPEO_MAX_WEEKLY_UNITS,
            analysis_end=analysis_end,
            target_label=target["label"],
            range_fallback=target.get("range_fallback", False),
        ),
        view=MapeoAnalysisView(
            analysis,
            scanned,
            analysis_start,
            latest_event_at,
            analysis_end,
            target["snapshot_id"],
            target["label"],
            target.get("range_fallback", False),
        ),
    )


async def enrich_mapping_event_player(guild: discord.Guild, event):
    if not event.discord_id:
        return

    member = guild.get_member(int(event.discord_id))
    if not member:
        try:
            member = await guild.fetch_member(int(event.discord_id))
        except (discord.HTTPException, ValueError):
            member = None

    if member and not member.bot:
        event.player = member.display_name


def get_mapeo_analysis_start(week_start: datetime):
    checkpoint = get_bot_state(MAPEO_ANALYSIS_CHECKPOINT_KEY)
    if not checkpoint:
        return week_start

    try:
        checkpoint_at = datetime.fromisoformat(checkpoint)
    except ValueError:
        return week_start

    if checkpoint_at.tzinfo is None:
        checkpoint_at = checkpoint_at.replace(tzinfo=timezone.utc)
    return max(week_start, checkpoint_at)


def parse_snapshot_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_mapeo_snapshot_range(snapshot):
    analysis_end = parse_snapshot_datetime(snapshot[3]) or parse_snapshot_datetime(snapshot[1])
    analysis_start = parse_snapshot_datetime(snapshot[2])
    used_fallback = False
    if not analysis_end:
        analysis_end = datetime.now(timezone.utc)
    if not analysis_start or analysis_start >= analysis_end:
        analysis_start = analysis_end - timedelta(days=7)
        used_fallback = True
    return analysis_start, analysis_end, used_fallback


def get_mapeo_count_target(source: str = "actual"):
    source = normalize_priority_source(source)
    if source == "ultimo_cierre":
        snapshot = get_latest_ranking_snapshot()
        if not snapshot:
            return {"missing": True}

        analysis_start, analysis_end, range_fallback = normalize_mapeo_snapshot_range(snapshot)
        return {
            "snapshot_id": int(snapshot[0]),
            "label": f"Cierre semanal #{snapshot[0]} ({snapshot[3]})",
            "analysis_start": analysis_start,
            "analysis_end": analysis_end,
            "range_fallback": range_fallback,
            "missing": False,
        }

    week_start = current_weekly_ranking_start()
    return {
        "snapshot_id": None,
        "label": "Ranking actual",
        "analysis_start": get_mapeo_analysis_start(week_start),
        "analysis_end": None,
        "range_fallback": False,
        "missing": False,
    }


def build_mapeo_analysis_embed(
    analysis: dict,
    scanned: int,
    analysis_start: datetime,
    max_units: int = MAPEO_MAX_WEEKLY_UNITS,
    road_weight: float = MAPEO_ROAD_WEIGHT,
    priority_weight: float = MAPEO_PRIORITY_WEIGHT,
    relock_weight: float = MAPEO_RELOCK_WEIGHT,
    analysis_end: datetime | None = None,
    target_label: str = "Ranking actual",
    range_fallback: bool = False,
    status_text: str | None = None,
    color: int = COLOR_WARNING,
):
    analysis = apply_mapeo_score_settings(analysis, road_weight, priority_weight, relock_weight)
    summary = analysis["summary"]
    mapeo_value = get_puntos("mapeo") or 1
    total_weight = sum(row["score"] for row in analysis["ranking"])
    top_weight = max((row["score"] for row in analysis["ranking"]), default=0)
    embed = discord.Embed(
        title="Analisis de mapeo",
        description=(
            f"Canal: <#{MAPEO_LOG_CHANNEL_ID}>\n"
            f"Destino: **{target_label}**\n"
            f"Desde: `{analysis_start.strftime('%Y-%m-%d %H:%M UTC')}`\n"
            f"Hasta: `{analysis_end.strftime('%Y-%m-%d %H:%M UTC') if analysis_end else 'ahora'}`\n"
            f"Mensajes revisados: `{scanned}`\n"
            f"Eventos detectados: `{summary['total_events']}`\n"
            f"Build: `{BOT_BUILD}`"
        ),
        color=color,
    )
    if range_fallback:
        embed.add_field(
            name="Rango corregido",
            value="El cierre tenia inicio y fin iguales; use automaticamente los 7 dias anteriores al cierre.",
            inline=False,
        )
    embed.add_field(
        name="Parametros",
        value=(
            f"Valor Mapeo: `{mapeo_value}` pt por unidad\n"
            f"Tope mejor aporte: `{max_units}` unidades x `{mapeo_value}` pt Mapeo = `{max_units * mapeo_value}` pts\n"
            f"Pesos: `Road {mapping_analysis.format_score(road_weight)} | Priority {mapping_analysis.format_score(priority_weight)} | RELOCK {mapping_analysis.format_score(relock_weight)}`\n"
            f"Curva: `exponencial x{mapping_analysis.format_score(MAPEO_SCALING_EXPONENT)}`\n"
            f"Peso top: `{mapping_analysis.format_score(top_weight)}` | Peso total: `{mapping_analysis.format_score(total_weight)}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Resumen general",
        value=(
            f"Rutas agregadas: `{summary['road_total']}`\n"
            f"Rutas unicas: `{summary['road_unique']}`\n"
            f"Prioridades: `{summary['priority_total']}`\n"
            f"Relocks: `{summary['relock_total']}`\n"
            f"Jugador mas activo: **{summary['most_active']}**\n"
            f"Mejor aporte estrategico: **{summary['best_strategic']}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Criterio",
        value=(
            "Solo la primera ruta `From -> To` cuenta como ruta util. "
            "Duplicados suman `0`. El mejor aporte recibe el tope de unidades; los aportes menores bajan con curva exponencial y el bot multiplica esas unidades por el valor de Mapeo."
        ),
        inline=False,
    )
    embed.add_field(
        name="Ranking",
        value=mapping_analysis.build_ranking_table(analysis["ranking"], max_units, mapeo_value, exponent=MAPEO_SCALING_EXPONENT),
        inline=False,
    )
    if status_text:
        embed.add_field(name="Estado", value=status_text, inline=False)
    return embed


def build_mapeo_analysis_files(analysis: dict):
    mapeo_value = get_puntos("mapeo") or 1
    return [
        discord.File(mapping_analysis.ranking_csv_bytes(analysis["ranking"], MAPEO_MAX_WEEKLY_UNITS, mapeo_value, MAPEO_SCALING_EXPONENT), filename="mapeo_ranking.csv"),
        discord.File(mapping_analysis.duplicates_csv_bytes(analysis["duplicates"]), filename="mapeo_duplicados.csv"),
        discord.File(mapping_analysis.events_csv_bytes(analysis["events"]), filename="mapeo_eventos.csv"),
    ]


def build_mapeo_unit_awards(analysis: dict, max_units: int):
    ranking = analysis["ranking"]
    top_weight = max((row["score"] for row in ranking), default=0)
    awards = []
    skipped = []
    for row in ranking:
        units = mapping_analysis.final_units_for_row(row, top_weight, max_units, MAPEO_SCALING_EXPONENT)
        if units <= 0:
            continue
        if not row.get("discord_id"):
            skipped.append(row["player"])
            continue
        awards.append((row["discord_id"], row["player"], units))
    return awards, skipped


def apply_mapeo_score_settings(
    analysis: dict,
    road_weight: float,
    priority_weight: float,
    relock_weight: float,
):
    adjusted = dict(analysis)
    ranking = []
    for row in analysis["ranking"]:
        item = dict(row)
        item["strategic_score"] = (item["priority"] * priority_weight) + (item["relock"] * relock_weight)
        item["score"] = (item["road_unique"] * road_weight) + item["strategic_score"]
        ranking.append(item)

    ranking.sort(
        key=lambda row: (-row["score"], -row["road_unique"], -row["strategic_score"], row["player"].lower())
    )
    for index, row in enumerate(ranking, start=1):
        row["rank"] = index

    summary = dict(analysis["summary"])
    strategic_rows = [row for row in ranking if row["strategic_score"] > 0]
    best_strategic = max(strategic_rows, key=lambda row: row["strategic_score"], default=None)
    summary["best_strategic"] = best_strategic["player"] if best_strategic else "N/A"
    adjusted["ranking"] = ranking
    adjusted["summary"] = summary
    return adjusted


class MapeoAnalysisView(SafeView):
    def __init__(
        self,
        analysis: dict,
        scanned: int,
        analysis_start: datetime,
        latest_event_at: datetime | None,
        analysis_end: datetime | None = None,
        target_snapshot_id: int | None = None,
        target_label: str = "Ranking actual",
        range_fallback: bool = False,
    ):
        super().__init__(timeout=1800)
        self.analysis = analysis
        self.scanned = scanned
        self.analysis_start = analysis_start
        self.latest_event_at = latest_event_at
        self.analysis_end = analysis_end
        self.target_snapshot_id = target_snapshot_id
        self.target_label = target_label
        self.range_fallback = range_fallback

    @discord.ui.button(label="Enviar a administrar evidencias", style=discord.ButtonStyle.success)
    async def send_to_review(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_review_member(interaction.user):
            await interaction.response.send_message("No tienes permiso para confirmar este analisis.", ephemeral=True)
            return

        await interaction.response.defer()

        review_channel = interaction.client.get_channel(EVIDENCE_REVIEW_CHANNEL_ID)
        if not review_channel:
            try:
                review_channel = await interaction.client.fetch_channel(EVIDENCE_REVIEW_CHANNEL_ID)
            except discord.HTTPException:
                await interaction.followup.send("No pude abrir administrar evidencias.", ephemeral=True)
                return

        embed = build_mapeo_analysis_embed(
            self.analysis,
            self.scanned,
            self.analysis_start,
            MAPEO_MAX_WEEKLY_UNITS,
            analysis_end=self.analysis_end,
            target_label=self.target_label,
            range_fallback=self.range_fallback,
            status_text=f"Pendiente de aprobacion. Enviado por {interaction.user.mention}",
        )
        review_view = MapeoReviewView(
            self.analysis,
            self.scanned,
            self.analysis_start,
            self.latest_event_at,
            self.analysis_end,
            self.target_snapshot_id,
            self.target_label,
            self.range_fallback,
        )
        review_message = await review_channel.send(embed=embed, view=review_view)
        review_view.message = review_message

        for item in self.children:
            item.disabled = True
        await interaction.message.edit(
            content="Analisis enviado a administrar evidencias para revision.",
            embed=build_mapeo_analysis_embed(
                self.analysis,
                self.scanned,
                self.analysis_start,
                MAPEO_MAX_WEEKLY_UNITS,
                analysis_end=self.analysis_end,
                target_label=self.target_label,
                range_fallback=self.range_fallback,
            ),
            view=self,
        )


class MapeoReviewView(SafeView):
    def __init__(
        self,
        analysis: dict,
        scanned: int,
        analysis_start: datetime,
        latest_event_at: datetime | None,
        analysis_end: datetime | None = None,
        target_snapshot_id: int | None = None,
        target_label: str = "Ranking actual",
        range_fallback: bool = False,
        max_units: int = MAPEO_MAX_WEEKLY_UNITS,
        road_weight: float = MAPEO_ROAD_WEIGHT,
        priority_weight: float = MAPEO_PRIORITY_WEIGHT,
        relock_weight: float = MAPEO_RELOCK_WEIGHT,
    ):
        super().__init__(timeout=86400)
        self.analysis = analysis
        self.scanned = scanned
        self.analysis_start = analysis_start
        self.latest_event_at = latest_event_at
        self.analysis_end = analysis_end
        self.target_snapshot_id = target_snapshot_id
        self.target_label = target_label
        self.range_fallback = range_fallback
        self.max_units = max_units
        self.road_weight = road_weight
        self.priority_weight = priority_weight
        self.relock_weight = relock_weight
        self.message: discord.Message | None = None

    def embed(self, status_text=None, color=COLOR_WARNING):
        return build_mapeo_analysis_embed(
            self.analysis,
            self.scanned,
            self.analysis_start,
            self.max_units,
            self.road_weight,
            self.priority_weight,
            self.relock_weight,
            analysis_end=self.analysis_end,
            target_label=self.target_label,
            range_fallback=self.range_fallback,
            status_text=status_text,
            color=color,
        )

    def adjusted_analysis(self):
        return apply_mapeo_score_settings(
            self.analysis,
            self.road_weight,
            self.priority_weight,
            self.relock_weight,
        )

    @discord.ui.button(label="Tope unidades", style=discord.ButtonStyle.secondary)
    async def change_units(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_review_member(interaction.user):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        await interaction.response.send_modal(MapeoMaxUnitsModal(self))

    @discord.ui.button(label="Valor Mapeo", style=discord.ButtonStyle.secondary)
    async def change_mapeo_value(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_review_member(interaction.user):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        await interaction.response.send_modal(MapeoActivityValueModal(self))

    @discord.ui.button(label="Pesos", style=discord.ButtonStyle.secondary)
    async def change_weights(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_review_member(interaction.user):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        await interaction.response.send_modal(MapeoScoringModal(self))

    @discord.ui.button(label="Aprobar", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_review_member(interaction.user):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return

        source_scope = f"cierre:{self.target_snapshot_id}" if self.target_snapshot_id else "actual"
        source_key = f"mapeo:{source_scope}:{self.latest_event_at.isoformat() if self.latest_event_at else self.analysis_start.isoformat()}"
        if get_bot_state(f"applied:{source_key}"):
            await interaction.response.send_message("Estos puntos ya fueron aplicados antes.", ephemeral=True)
            return

        awards, skipped = build_mapeo_unit_awards(self.adjusted_analysis(), self.max_units)
        if not awards:
            await interaction.response.send_message("No hay usuarios con ID para aplicar puntos.", ephemeral=True)
            return

        total_points = 0
        mapeo_value = get_puntos("mapeo") or 0
        failed = []
        applied_units_total = 0
        for user_id, username, units in awards:
            if self.target_snapshot_id:
                result = adjust_snapshot_activity(int(self.target_snapshot_id), str(user_id), username, "mapeo", int(units))
                if not result.get("ok"):
                    failed.append(f"{username}: {snapshot_adjust_error_text(result)}")
                    continue
                total_points += result["points"]
                applied_units_total += result["applied_units"]
            else:
                total_points += add_activity(str(user_id), username, "mapeo", int(units))
                applied_units_total += int(units)
        set_bot_state(f"applied:{source_key}", datetime.now(timezone.utc).isoformat())

        if self.latest_event_at and not self.target_snapshot_id:
            set_bot_state(MAPEO_ANALYSIS_CHECKPOINT_KEY, self.latest_event_at.isoformat())

        for item in self.children:
            item.disabled = True
        skipped_text = f" No aplicados sin ID: {', '.join(skipped[:5])}." if skipped else ""
        failed_text = f" No aplicados: {', '.join(failed[:5])}." if failed else ""
        await interaction.response.edit_message(
            embed=self.embed(
                f"Aprobado por {interaction.user.mention}. "
                f"Destino: **{self.target_label}**. "
                f"Aplicadas `{applied_units_total}` unidades de mapeo x `{mapeo_value}` pt = `{total_points}` pts a `{len(awards) - len(failed)}` jugadores. "
                f"{'Rango cerrado para futuros analisis.' if not self.target_snapshot_id else 'El ranking actual no fue modificado.'}{skipped_text}{failed_text}",
                COLOR_SUCCESS,
            ),
            view=self,
        )
        if not self.target_snapshot_id:
            await publish_or_update_dashboard()
            await publish_or_update_info_ranking()

    @discord.ui.button(label="Rechazar", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_review_member(interaction.user):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=self.embed(f"Rechazado por {interaction.user.mention}. No se cerro el rango.", COLOR_ERROR),
            view=self,
        )


class MapeoMaxUnitsModal(SafeModal):
    value = discord.ui.TextInput(label="Tope unidades del mejor aporte", placeholder="Ej: 30", max_length=3)

    def __init__(self, review_view: MapeoReviewView):
        super().__init__(title="Cambiar tope de mapeo")
        self.review_view = review_view
        self.value.default = str(review_view.max_units)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            max_units = int(str(self.value.value).strip())
            if max_units <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Ingresa un numero mayor a 0.", ephemeral=True)
            return

        self.review_view.max_units = max_units
        await interaction.response.defer(ephemeral=True)
        if self.review_view.message:
            await self.review_view.message.edit(
                embed=self.review_view.embed(f"Tope ajustado por {interaction.user.mention}."),
                view=self.review_view,
            )
        await interaction.followup.send("Tope de unidades actualizado.", ephemeral=True)


class MapeoActivityValueModal(SafeModal):
    value = discord.ui.TextInput(label="Valor de cada unidad Mapeo", placeholder="Ej: 3", max_length=3)

    def __init__(self, review_view: MapeoReviewView):
        super().__init__(title="Cambiar valor Mapeo")
        self.review_view = review_view
        self.value.default = str(get_puntos("mapeo") or 1)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            mapeo_value = int(str(self.value.value).strip())
            if mapeo_value <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Ingresa un numero mayor a 0.", ephemeral=True)
            return

        set_puntos("mapeo", mapeo_value)
        await interaction.response.defer(ephemeral=True)
        if self.review_view.message:
            await self.review_view.message.edit(
                embed=self.review_view.embed(f"Valor Mapeo ajustado por {interaction.user.mention}."),
                view=self.review_view,
            )
        await publish_or_update_dashboard()
        await publish_or_update_info_ranking()
        await interaction.followup.send("Valor de Mapeo actualizado.", ephemeral=True)


class MapeoScoringModal(SafeModal):
    road_weight = discord.ui.TextInput(label="Peso Road unica", placeholder="Ej: 1", max_length=5)
    priority_weight = discord.ui.TextInput(label="Peso Priority", placeholder="Ej: 0.15", max_length=5)
    relock_weight = discord.ui.TextInput(label="Peso RELOCK", placeholder="Ej: 0.15", max_length=5)

    def __init__(self, review_view: MapeoReviewView):
        super().__init__(title="Cambiar pesos de mapeo")
        self.review_view = review_view
        self.road_weight.default = mapping_analysis.format_score(review_view.road_weight)
        self.priority_weight.default = mapping_analysis.format_score(review_view.priority_weight)
        self.relock_weight.default = mapping_analysis.format_score(review_view.relock_weight)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            road_weight = float(str(self.road_weight.value).strip().replace(",", "."))
            priority_weight = float(str(self.priority_weight.value).strip().replace(",", "."))
            relock_weight = float(str(self.relock_weight.value).strip().replace(",", "."))
            if road_weight <= 0 or priority_weight < 0 or relock_weight < 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Ingresa valores validos. Road debe ser mayor a 0.", ephemeral=True)
            return

        self.review_view.road_weight = road_weight
        self.review_view.priority_weight = priority_weight
        self.review_view.relock_weight = relock_weight
        await interaction.response.defer(ephemeral=True)
        if self.review_view.message:
            await self.review_view.message.edit(
                embed=self.review_view.embed(f"Pesos ajustados por {interaction.user.mention}."),
                view=self.review_view,
            )
        await interaction.followup.send("Pesos de mapeo actualizados.", ephemeral=True)


@admin_group.command(name="reset_analisis", description="Reinicia el checkpoint semanal del analisis de mapeo")
async def reset_analisis_mapeo(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return

    week_start = current_weekly_ranking_start()
    set_bot_state(MAPEO_ANALYSIS_CHECKPOINT_KEY, week_start.isoformat())
    await interaction.response.send_message(
        f"Checkpoint de mapeo reiniciado a `{week_start.strftime('%Y-%m-%d %H:%M UTC')}`.",
        ephemeral=True,
    )


@admin_group.command(name="dashboard_scouts", description="Publica o actualiza el dashboard de scouts")
async def dashboard_scouts(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return

    await publish_or_update_dashboard()
    await interaction.response.send_message("Dashboard actualizado.", ephemeral=True)


async def publish_or_update_dashboard():
    channel = bot.get_channel(DASHBOARD_CHANNEL_ID)
    if not channel:
        channel = await bot.fetch_channel(DASHBOARD_CHANNEL_ID)

    embed = build_dashboard_embed()
    dashboard_msg = None
    async for msg in channel.history(limit=20):
        if msg.author.id == bot.user.id and msg.embeds and "Dashboard Scouts" in (msg.embeds[0].title or ""):
            dashboard_msg = msg
            break

    if dashboard_msg:
        await dashboard_msg.edit(embed=embed, view=DashboardView())
    else:
        await channel.send(embed=embed, view=DashboardView())

# ── Run ───────────────────────────────────────────────────────────────────────

@tree.command(name="mi_ranking", description="Muestra tu perfil y puntos de scout")
async def mi_ranking(interaction: discord.Interaction):
    embed = build_perfil_embed(str(interaction.user.id), interaction.user.display_name)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@admin_group.command(name="perfil", description="Muestra el perfil y puntos de cualquier scout")
@app_commands.describe(usuario="Scout a revisar")
async def admin_perfil(interaction: discord.Interaction, usuario: discord.Member):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return

    embed = build_perfil_embed(str(usuario.id), usuario.display_name)
    await interaction.response.send_message(embed=embed, view=AdminProfileView(usuario), ephemeral=True)


class AdminProfileView(SafeView):
    def __init__(self, usuario: discord.Member):
        super().__init__(timeout=300)
        self.user_id = str(usuario.id)
        self.display_name = usuario.display_name

    @discord.ui.button(label="Sumar", style=discord.ButtonStyle.success)
    async def add_points(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=build_admin_profile_action_embed(self.user_id, self.display_name, "sumar"),
            view=AdminProfileActivityView(self.user_id, self.display_name, "sumar"),
            ephemeral=True,
        )

    @discord.ui.button(label="Restar", style=discord.ButtonStyle.danger)
    async def subtract_points(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=build_admin_profile_action_embed(self.user_id, self.display_name, "restar"),
            view=AdminProfileActivityView(self.user_id, self.display_name, "restar"),
            ephemeral=True,
        )

    @discord.ui.button(label="Actualizar", style=discord.ButtonStyle.secondary)
    async def refresh_profile(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=build_perfil_embed(self.user_id, self.display_name),
            view=self,
        )


class AdminProfileActivityView(SafeView):
    def __init__(self, user_id: str, display_name: str, action: str):
        super().__init__(timeout=180)
        self.user_id = str(user_id)
        self.display_name = display_name
        self.action = action
        for key, meta in ACTIVIDADES.items():
            self.add_item(AdminProfileActivityButton(self.user_id, self.display_name, self.action, key, meta))


class AdminProfileActivityButton(discord.ui.Button):
    def __init__(self, user_id: str, display_name: str, action: str, actividad_key: str, meta: dict):
        super().__init__(
            label=meta["label"],
            emoji=meta["emoji"],
            style=discord.ButtonStyle.success if action == "sumar" else discord.ButtonStyle.danger,
        )
        self.user_id = str(user_id)
        self.display_name = display_name
        self.action = action
        self.actividad_key = actividad_key

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        await interaction.response.send_modal(
            AdminProfilePointsModal(self.user_id, self.display_name, self.action, self.actividad_key)
        )


class AdminProfilePointsModal(SafeModal):
    cantidad = discord.ui.TextInput(label="Cantidad", placeholder="Ej: 24", max_length=6)
    tipo = discord.ui.TextInput(label="Tipo", placeholder="unidades o puntos", default="unidades", max_length=12)
    motivo = discord.ui.TextInput(
        label="Motivo",
        placeholder="Ej: ajuste por pelea, correccion AFK",
        required=False,
        max_length=120,
    )

    def __init__(self, user_id: str, display_name: str, action: str, actividad_key: str):
        self.user_id = str(user_id)
        self.display_name = display_name
        self.action = action
        self.actividad_key = actividad_key
        meta = ACTIVIDADES[actividad_key]
        verb = "Sumar" if action == "sumar" else "Restar"
        super().__init__(title=f"{verb} - {meta['label']}")

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return

        units, requested_points, error = parse_activity_amount(
            self.actividad_key,
            str(self.cantidad.value),
            str(self.tipo.value or "unidades"),
        )
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        if self.action == "sumar":
            changed_points = add_activity(self.user_id, self.display_name, self.actividad_key, units)
            color = COLOR_SUCCESS
            sign = "+"
        else:
            changed_points = subtract_activity(self.user_id, self.display_name, self.actividad_key, units)
            color = COLOR_ERROR
            sign = "-"

        await publish_or_update_dashboard()
        await publish_or_update_info_ranking()
        embed = build_admin_profile_adjustment_embed(
            self.user_id,
            self.display_name,
            self.actividad_key,
            self.action,
            units,
            requested_points,
            changed_points,
            sign,
            color,
            str(self.motivo.value or ""),
        )
        await interaction.response.send_message(
            embed=embed,
            view=AdminProfileView(SimpleProfileUser(self.user_id, self.display_name)),
            ephemeral=True,
        )


class SimpleProfileUser:
    def __init__(self, user_id: str, display_name: str):
        self.id = int(user_id) if str(user_id).isdigit() else user_id
        self.display_name = display_name


def build_admin_profile_action_embed(user_id: str, display_name: str, action: str):
    verb = "sumar" if action == "sumar" else "restar"
    embed = discord.Embed(
        title=f"Elegir actividad para {verb}",
        description=f"Scout: <@{user_id}>\nElige la actividad que quieres {verb}.",
        color=COLOR_SUCCESS if action == "sumar" else COLOR_ERROR,
    )
    return embed


def build_admin_profile_adjustment_embed(
    user_id: str,
    display_name: str,
    actividad_key: str,
    action: str,
    units: int,
    requested_points: int,
    changed_points: int,
    sign: str,
    color: int,
    motivo: str,
):
    meta = ACTIVIDADES[actividad_key]
    embed = discord.Embed(
        title="Perfil ajustado",
        description=(
            f"Scout: <@{user_id}>\n"
            f"{meta['emoji']} **{meta['label']}**\n"
            f"Accion: **{action}**\n"
            f"Cantidad: `{units}` unidades = `{requested_points}` pts solicitados\n"
            f"Puntos aplicados: `{sign}{changed_points}`"
        ),
        color=color,
    )
    if motivo.strip():
        embed.add_field(name="Motivo", value=motivo.strip()[:1000], inline=False)
    embed.set_footer(text=f"Perfil de {display_name} actualizado.")
    return embed


@admin_group.command(name="prio", description="Panel semanal para revisar y aplicar el rol prio")
@app_commands.describe(
    minimo="Puntos minimos para recibir prio. Ej: 50",
    fuente="Ranking actual o ultimo cierre semanal",
)
@app_commands.choices(fuente=[
    app_commands.Choice(name="Ranking actual", value="actual"),
    app_commands.Choice(name="Ultimo cierre semanal", value="ultimo_cierre"),
])
async def prio(
    interaction: discord.Interaction,
    minimo: int = DEFAULT_PRIORITY_MIN_POINTS,
    fuente: str = "actual",
):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return

    if minimo < 0:
        await interaction.response.send_message("El minimo no puede ser negativo.", ephemeral=True)
        return

    if not interaction.guild:
        await interaction.response.send_message("Este comando solo funciona dentro del servidor.", ephemeral=True)
        return

    role = interaction.guild.get_role(PRIORITY_ROLE_ID)
    if not role:
        await interaction.response.send_message(f"No encontre el rol prio `{PRIORITY_ROLE_ID}`.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        source = normalize_priority_source(fuente)
        embed = build_priority_dashboard_embed(interaction.guild, role, minimo, source)
    except Exception as err:
        traceback.print_exc()
        await interaction.followup.send(f"No pude construir el panel de prio: `{err}`", ephemeral=True)
        return

    await interaction.followup.send(embed=embed, view=PrioDashboardView(minimo, source), ephemeral=True)


@admin_group.command(name="info_ranking", description="Publica la guía y ranking general")
async def info_ranking(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return

    await publish_or_update_info_ranking()
    await interaction.response.send_message("Info ranking actualizada.", ephemeral=True)


async def publish_or_update_info_ranking():
    channel = bot.get_channel(INFO_RANKING_CHANNEL_ID)
    if not channel:
        channel = await bot.fetch_channel(INFO_RANKING_CHANNEL_ID)

    embed = build_info_ranking_embed()
    info_msg = None
    async for msg in channel.history(limit=20):
        if msg.author.id == bot.user.id and msg.embeds and "Ranking de Evidencias" in (msg.embeds[0].title or ""):
            info_msg = msg
            break

    if info_msg:
        await info_msg.edit(embed=embed, view=InfoRankingView())
    else:
        await channel.send(embed=embed, view=InfoRankingView())


@admin_group.command(name="modificar_puntos", description="Suma o resta actividades a un scout")
@app_commands.choices(actividad=ACT_CHOICES)
@app_commands.choices(fuente=RANKING_SOURCE_CHOICES)
async def modificar_puntos(
    interaction: discord.Interaction,
    usuario: discord.Member,
    actividad: app_commands.Choice[str],
    cantidad: int,
    fuente: str = "actual",
):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return

    if cantidad == 0:
        await interaction.response.send_message("Cantidad no puede ser 0.", ephemeral=True)
        return

    actividad_key = actividad.value
    source = normalize_priority_source(fuente)
    snapshot = None
    if source == "ultimo_cierre":
        snapshot = get_latest_ranking_snapshot()
        if not snapshot:
            await interaction.response.send_message("Aun no hay cierre semanal guardado para ajustar.", ephemeral=True)
            return
        result = adjust_snapshot_activity(
            int(snapshot[0]),
            str(usuario.id),
            usuario.display_name,
            actividad_key,
            cantidad,
        )
        if not result.get("ok"):
            await interaction.response.send_message(
                format_snapshot_adjust_error(result, usuario),
                ephemeral=True,
            )
            return
        pts = result["points"]
        signo = "+" if cantidad > 0 else "-"
        applied_quantity = result["applied_units"] if cantidad > 0 else -result["applied_units"]
        source_label = f"Ultimo cierre semanal #{snapshot[0]} ({snapshot[3]})"
    else:
        if cantidad > 0:
            pts = add_activity(str(usuario.id), usuario.display_name, actividad_key, cantidad)
            signo = "+"
        else:
            pts = subtract_activity(str(usuario.id), usuario.display_name, actividad_key, abs(cantidad))
            signo = "-"
        applied_quantity = cantidad
        source_label = "Ranking actual"

    meta = ACTIVIDADES[actividad_key]
    embed = discord.Embed(
        description=(
            f"{meta['emoji']} **{meta['label']}**\n"
            f"Usuario: {usuario.mention}\n"
            f"Fuente: **{source_label}**\n"
            f"Cantidad aplicada: `{applied_quantity}`\n"
            f"Puntos: `{signo}{pts}`"
        ),
        color=COLOR_SUCCESS if cantidad > 0 else COLOR_ERROR,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@admin_group.command(name="puntos", description="Panel para sumar o restar puntos en masa")
@app_commands.choices(fuente=RANKING_SOURCE_CHOICES)
async def puntos(interaction: discord.Interaction, fuente: str = "actual"):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        source = normalize_priority_source(fuente)
        if source == "ultimo_cierre" and not get_latest_ranking_snapshot():
            await interaction.followup.send("Aun no hay cierre semanal guardado para ajustar.", ephemeral=True)
            return
        embed = build_bulk_points_dashboard_embed(source)
    except Exception as err:
        traceback.print_exc()
        await interaction.followup.send(f"No pude construir el panel de puntos: `{err}`", ephemeral=True)
        return

    await interaction.followup.send(embed=embed, view=BulkPointsDashboardView(source), ephemeral=True)


def build_bulk_points_dashboard_embed(source: str = "actual"):
    points_config = {activity: points for activity, points in get_all_config()}
    activity_lines = [
        f"{meta['emoji']} **{meta['label']}** - `{points_config.get(key, 0)} pts/unidad`"
        for key, meta in ACTIVIDADES.items()
    ]
    source_data = get_ranking_export_source(source)
    embed = discord.Embed(
        title="Carga masiva de puntos",
        description=(
            "Elige una actividad y pega nombres con `+`, menciones o IDs.\n"
            "Puedes ingresar la cantidad como `unidades` o como `puntos` finales.\n"
            f"Fuente: **{source_data['label']}**"
        ),
        color=COLOR_PANEL,
    )
    embed.add_field(name="Actividades", value="\n".join(activity_lines), inline=False)
    embed.add_field(
        name="Ejemplo",
        value=(
            "`+ferthor +ryanshaft +viohlet +chinojvs`\n"
            "Cantidad: `24` | Tipo: `puntos` | Actividad: `Kill Pelea`\n"
            "Si Kill Pelea vale 3 pts, el bot suma 8 unidades a cada uno."
        ),
        inline=False,
    )
    return embed


class BulkPointsDashboardView(SafeView):
    def __init__(self, source: str = "actual"):
        super().__init__(timeout=300)
        self.source = normalize_priority_source(source)
        for key, meta in ACTIVIDADES.items():
            self.add_item(BulkPointsActivityButton(key, meta, self.source))


class BulkPointsActivityButton(discord.ui.Button):
    def __init__(self, actividad_key: str, meta: dict, source: str = "actual"):
        super().__init__(
            label=meta["label"],
            emoji=meta["emoji"],
            style=discord.ButtonStyle.primary if actividad_key == "kill_pelea" else discord.ButtonStyle.secondary,
        )
        self.actividad_key = actividad_key
        self.source = normalize_priority_source(source)

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        await interaction.response.send_modal(BulkPointsModal(self.actividad_key, self.source))


def parse_activity_amount(actividad_key: str, amount_text: str, quantity_type_text: str):
    try:
        amount = int(str(amount_text).strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        return 0, 0, "Ingresa una cantidad mayor a 0."

    quantity_type = str(quantity_type_text or "unidades").strip().lower()
    activity_points = get_puntos(actividad_key)
    if quantity_type.startswith("p"):
        if activity_points <= 0:
            return 0, 0, "Esta actividad no tiene valor de puntos configurado."
        if amount % activity_points != 0:
            return (
                0,
                0,
                f"`{amount}` puntos no se puede convertir exacto a unidades de {ACTIVIDADES[actividad_key]['label']} "
                f"porque cada unidad vale `{activity_points}` pts.",
            )
        return amount // activity_points, amount, None

    if quantity_type.startswith("u") or not quantity_type:
        return amount, amount * activity_points, None

    return 0, 0, "Tipo invalido. Usa `unidades` o `puntos`."


def normalize_points_action(value: str | None):
    text = str(value or "sumar").strip().lower()
    if text in {"sumar", "suma", "+", "add"}:
        return "sumar"
    if text in {"restar", "resta", "-", "subtract"}:
        return "restar"
    return None


def snapshot_adjust_error_text(result: dict):
    reason = result.get("reason")
    if reason == "snapshot_not_found":
        return "no encontre ese cierre"
    if reason == "zero_quantity":
        return "cantidad cero"
    if reason == "snapshot_user_not_found":
        return "no estaba en el cierre"
    if reason == "nothing_to_subtract":
        return "no tiene unidades para restar"
    return reason or "no se pudo ajustar"


def format_snapshot_adjust_error(result: dict, usuario: discord.Member):
    return f"No pude ajustar el cierre para {usuario.mention}: {snapshot_adjust_error_text(result)}."


class BulkPointsModal(SafeModal):
    accion = discord.ui.TextInput(label="Accion", placeholder="sumar o restar", default="sumar", max_length=8)
    cantidad = discord.ui.TextInput(label="Cantidad", placeholder="Ej: 24", max_length=6)
    tipo = discord.ui.TextInput(label="Tipo", placeholder="unidades o puntos", default="unidades", max_length=12)
    nombres = discord.ui.TextInput(
        label="Nombres, menciones o IDs",
        placeholder="+ferthor +ryanshaft +viohlet +monitougly +chinojvs +tikilon +shortout",
        style=discord.TextStyle.paragraph,
        max_length=1200,
    )
    motivo = discord.ui.TextInput(
        label="Motivo",
        placeholder="Ej: pelea del 14/06, 24 pts",
        required=False,
        max_length=120,
    )

    def __init__(self, actividad_key: str, source: str = "actual"):
        self.actividad_key = actividad_key
        self.source = normalize_priority_source(source)
        meta = ACTIVIDADES[actividad_key]
        super().__init__(title=f"Carga masiva - {meta['label']}")

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return

        action = normalize_points_action(str(self.accion.value or "sumar"))
        if not action:
            await interaction.response.send_message("Accion invalida. Usa `sumar` o `restar`.", ephemeral=True)
            return

        units, requested_points, error = parse_activity_amount(
            self.actividad_key,
            str(self.cantidad.value),
            str(self.tipo.value or "unidades"),
        )
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        participants, unresolved, suggestions = await participant_tools.resolve_manual_names(
            interaction.guild,
            str(self.nombres.value),
        )

        if not participants:
            embed = build_bulk_points_result_embed(
                self.actividad_key,
                units,
                requested_points,
                [],
                unresolved,
                suggestions,
                str(self.motivo.value or ""),
                action,
                self.source,
                get_latest_ranking_snapshot() if self.source == "ultimo_cierre" else None,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        snapshot = get_latest_ranking_snapshot() if self.source == "ultimo_cierre" else None
        if self.source == "ultimo_cierre" and not snapshot:
            await interaction.followup.send("Aun no hay cierre semanal guardado para ajustar.", ephemeral=True)
            return

        applied = []
        for user_id, username in participants:
            signed_units = units if action == "sumar" else -units
            if self.source == "ultimo_cierre":
                result = adjust_snapshot_activity(
                    int(snapshot[0]),
                    str(user_id),
                    username,
                    self.actividad_key,
                    signed_units,
                )
                if not result.get("ok"):
                    unresolved.append(f"{username}: {snapshot_adjust_error_text(result)}")
                    continue
                points = result["points"]
                applied_units = result["applied_units"]
            elif action == "sumar":
                points = add_activity(str(user_id), username, self.actividad_key, units)
                applied_units = units
            else:
                points = subtract_activity(str(user_id), username, self.actividad_key, units)
                applied_units = units
            applied.append((user_id, username, points, applied_units))

        if self.source == "actual":
            await publish_or_update_dashboard()
            await publish_or_update_info_ranking()
        embed = build_bulk_points_result_embed(
            self.actividad_key,
            units,
            requested_points,
            applied,
            unresolved,
            suggestions,
            str(self.motivo.value or ""),
            action,
            self.source,
            snapshot,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


def build_bulk_points_result_embed(
    actividad_key: str,
    units: int,
    requested_points: int,
    applied: list[tuple],
    unresolved: list[str],
    suggestions: list[dict],
    motivo: str,
    action: str = "sumar",
    source: str = "actual",
    snapshot=None,
):
    meta = ACTIVIDADES[actividad_key]
    color = COLOR_SUCCESS if applied else COLOR_WARNING
    source_data = get_ranking_export_source(source)
    sign = "+" if action == "sumar" else "-"
    embed = discord.Embed(
        title="Carga masiva aplicada" if applied else "Carga masiva sin aplicar",
        description=(
            f"{meta['emoji']} **{meta['label']}**\n"
            f"Accion: **{action}**\n"
            f"Fuente: **{source_data['label']}**\n"
            f"Cantidad por persona: `{units}` unidades = `{requested_points}` pts"
        ),
        color=color,
    )
    if motivo.strip():
        embed.add_field(name="Motivo", value=motivo.strip()[:1000], inline=False)
    if applied:
        total_points = sum(points for _, _, points, *_ in applied)
        lines = [
            f"<@{user_id}> - `{sign}{points}` pts"
            for user_id, _, points, *_ in applied[:25]
        ]
        embed.add_field(name=f"Aplicados ({len(applied)})", value="\n".join(lines), inline=False)
        embed.add_field(
            name="Total agregado" if action == "sumar" else "Total restado",
            value=f"`{sign}{total_points}` pts",
            inline=True,
        )
    if source == "ultimo_cierre" and snapshot:
        embed.set_footer(text=f"Cierre semanal #{snapshot[0]} ajustado.")
    if unresolved:
        embed.add_field(
            name="No encontrados",
            value=", ".join(f"`{name}`" for name in unresolved[:25])[:1000],
            inline=False,
        )
    if suggestions:
        embed.add_field(
            name="Sugerencias no aplicadas",
            value=participant_tools.format_participant_suggestions(suggestions)[:1000],
            inline=False,
        )
    return embed


@admin_group.command(name="padron", description="Panel para administrar el padron de aliases de scouts")
async def padron(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return

    await interaction.response.send_message(
        embed=build_alias_pattern_dashboard_embed(),
        view=AliasPatternDashboardView(),
        ephemeral=True,
    )


def build_alias_pattern_dashboard_embed():
    alias_rows = get_scout_aliases()
    scouts = get_all_scouts()
    embed = discord.Embed(
        title="Panel de padron",
        description=(
            "Administra nombres alternos de scouts desde una plantilla XLSX.\n"
            "La columna `aliases` acepta varios nombres separados por comas, espacios o saltos de linea."
        ),
        color=COLOR_PANEL,
    )
    embed.add_field(name="Scouts en ranking", value=str(len(scouts)), inline=True)
    embed.add_field(name="Aliases registrados", value=str(len(alias_rows)), inline=True)
    embed.add_field(name="Formato", value="`user_id | username | aliases`", inline=True)
    if alias_rows:
        preview = [
            f"<@{user_id}> - `{alias}`"
            for user_id, _, alias in alias_rows[:8]
        ]
        if len(alias_rows) > 8:
            preview.append(f"... y {len(alias_rows) - 8} mas")
        embed.add_field(name="Vista rapida", value="\n".join(preview), inline=False)
    return embed


class AliasPatternDashboardView(SafeView):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Actualizar", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        await interaction.response.edit_message(embed=build_alias_pattern_dashboard_embed(), view=self)

    @discord.ui.button(label="Exportar XLSX", style=discord.ButtonStyle.primary)
    async def export_xlsx(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        await interaction.response.send_message(
            content="Edita la columna `aliases` y vuelve a importarlo desde este panel.",
            file=build_alias_pattern_xlsx_file(),
            ephemeral=True,
        )

    @discord.ui.button(label="Importar XLSX", style=discord.ButtonStyle.success)
    async def import_xlsx(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        if not interaction.channel:
            await interaction.response.send_message("No encontre el canal para recibir el archivo.", ephemeral=True)
            return

        await interaction.response.send_message(
            "Sube el `.xlsx` en este canal durante los proximos 3 minutos. "
            "Debe tener columnas `user_id`, `username` y `aliases`.",
            ephemeral=True,
        )

        def check(message: discord.Message):
            if message.author.id != interaction.user.id or message.channel.id != interaction.channel.id:
                return False
            return any(attachment.filename.lower().endswith(".xlsx") for attachment in message.attachments)

        try:
            message = await bot.wait_for("message", timeout=180, check=check)
        except asyncio.TimeoutError:
            await interaction.followup.send("No recibi ningun `.xlsx` a tiempo.", ephemeral=True)
            return

        attachment = next(
            item for item in message.attachments
            if item.filename.lower().endswith(".xlsx")
        )
        await interaction.followup.send("Leyendo XLSX y registrando aliases...", ephemeral=True)
        try:
            content = await read_attachment_bytes(attachment)
            rows = parse_alias_pattern_xlsx(content)
            result = await import_alias_pattern_rows(interaction.guild, rows)
        except Exception as err:
            traceback.print_exception(type(err), err, err.__traceback__)
            await interaction.followup.send(f"No pude importar el XLSX: `{err}`", ephemeral=True)
            return

        await interaction.followup.send(embed=build_alias_import_result_embed(result), ephemeral=True)

    @discord.ui.button(label="Agregar manual", style=discord.ButtonStyle.secondary)
    async def add_manual(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        await interaction.response.send_modal(AliasManualAddModal())

    @discord.ui.button(label="Quitar alias", style=discord.ButtonStyle.danger)
    async def remove_manual(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        await interaction.response.send_modal(AliasManualRemoveModal())


class AliasManualAddModal(SafeModal):
    usuario = discord.ui.TextInput(
        label="Usuario",
        placeholder="Mencion, ID o nombre exacto del scout",
        max_length=120,
    )
    aliases = discord.ui.TextInput(
        label="Aliases",
        placeholder="z5655, zaitxs2, otroNombre",
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    def __init__(self):
        super().__init__(title="Agregar aliases")

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return

        target = await resolve_alias_target(interaction.guild, str(self.usuario.value), "")
        aliases = participant_tools.extract_manual_names(str(self.aliases.value))
        result = empty_alias_import_result()
        if not target:
            result["unresolved"].append(str(self.usuario.value))
        elif not aliases:
            result["empty"].append(target["username"])
        else:
            save_aliases_for_target(target, aliases, result)

        await interaction.response.send_message(embed=build_alias_import_result_embed(result), ephemeral=True)


class AliasManualRemoveModal(SafeModal):
    aliases = discord.ui.TextInput(
        label="Aliases a quitar",
        placeholder="z5655, otroNombre",
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    def __init__(self):
        super().__init__(title="Quitar aliases")

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return

        removed = []
        missing = []
        for alias in participant_tools.extract_manual_names(str(self.aliases.value)):
            if remove_scout_alias(alias):
                removed.append(alias)
            else:
                missing.append(alias)

        embed = discord.Embed(
            title="Aliases quitados" if removed else "No encontre aliases",
            color=COLOR_SUCCESS if removed else COLOR_WARNING,
        )
        if removed:
            embed.add_field(name="Quitados", value=", ".join(f"`+{alias}`" for alias in removed[:30])[:1000], inline=False)
        if missing:
            embed.add_field(name="No encontrados", value=", ".join(f"`+{alias}`" for alias in missing[:30])[:1000], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


def build_alias_pattern_xlsx_file():
    rows = [["user_id", "username", "aliases"]]
    aliases_by_user = {}
    usernames_by_user = {}
    for user_id, username, alias in get_scout_aliases():
        user_id = str(user_id)
        aliases_by_user.setdefault(user_id, []).append(alias)
        usernames_by_user[user_id] = username

    scout_rows = {str(row[0]): row[1] for row in get_all_scouts()}
    for user_id in sorted(set(scout_rows) | set(aliases_by_user), key=lambda item: (scout_rows.get(item) or usernames_by_user.get(item) or item).lower()):
        rows.append([
            user_id,
            scout_rows.get(user_id) or usernames_by_user.get(user_id) or "",
            ", ".join(aliases_by_user.get(user_id, [])),
        ])

    filename = f"padron_aliases_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.xlsx"
    return discord.File(fp=io.BytesIO(build_xlsx_bytes(rows, sheet_name="Padron")), filename=filename)


async def read_attachment_bytes(attachment: discord.Attachment):
    errors = []
    try:
        return await attachment.read(use_cached=False)
    except discord.HTTPException as err:
        errors.append(f"attachment.read: {err}")

    if attachment.url:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment.url) as response:
                    if response.status == 200:
                        return await response.read()
                    errors.append(f"GET {response.status}: {await response.text()}")
        except aiohttp.ClientError as err:
            errors.append(f"GET error: {err}")

    raise RuntimeError("; ".join(errors) or "No pude leer el archivo adjunto.")


def parse_alias_pattern_xlsx(content: bytes):
    rows = read_xlsx_rows(content)
    if not rows:
        return []

    header = [normalize_alias_header(cell) for cell in rows[0]]
    indexes = {
        "user_id": find_header_index(header, {"user_id", "userid", "discord_id", "discordid", "id"}),
        "username": find_header_index(header, {"username", "usuario", "scout", "nombre", "name"}),
        "aliases": find_header_index(header, {"aliases", "alts", "alt", "padron", "patrones", "patron", "nombres"}),
    }
    if indexes["aliases"] is None:
        raise ValueError("No encontre la columna aliases.")
    if indexes["user_id"] is None and indexes["username"] is None:
        raise ValueError("Necesito columna user_id o username.")

    parsed = []
    for row in rows[1:]:
        if not any(str(cell or "").strip() for cell in row):
            continue
        parsed.append({
            "user_id": cell_at(row, indexes["user_id"]),
            "username": cell_at(row, indexes["username"]),
            "aliases": cell_at(row, indexes["aliases"]),
        })
    return parsed


def read_xlsx_rows(content: bytes):
    with zipfile.ZipFile(io.BytesIO(content)) as xlsx:
        shared_strings = read_xlsx_shared_strings(xlsx)
        worksheet_name = find_first_worksheet_name(xlsx)
        xml = xlsx.read(worksheet_name)

    root = ET.fromstring(xml)
    rows = []
    for row in root.findall(".//{*}sheetData/{*}row"):
        values = []
        next_index = 1
        for cell in row.findall("{*}c"):
            ref = cell.attrib.get("r", "")
            col_index = column_index_from_ref(ref) or next_index
            while len(values) < col_index - 1:
                values.append("")
            values.append(read_xlsx_cell(cell, shared_strings))
            next_index = col_index + 1
        rows.append(values)
    return rows


def read_xlsx_shared_strings(xlsx: zipfile.ZipFile):
    if "xl/sharedStrings.xml" not in xlsx.namelist():
        return []
    root = ET.fromstring(xlsx.read("xl/sharedStrings.xml"))
    strings = []
    for item in root.findall("{*}si"):
        strings.append("".join(text.text or "" for text in item.findall(".//{*}t")))
    return strings


def find_first_worksheet_name(xlsx: zipfile.ZipFile):
    names = sorted(
        name for name in xlsx.namelist()
        if re.match(r"xl/worksheets/sheet\d+\.xml$", name)
    )
    if not names:
        raise ValueError("El XLSX no tiene hojas legibles.")
    return names[0]


def read_xlsx_cell(cell, shared_strings: list[str]):
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//{*}t")).strip()

    value_node = cell.find("{*}v")
    value = (value_node.text if value_node is not None else "") or ""
    if cell_type == "s":
        try:
            return shared_strings[int(value)].strip()
        except (ValueError, IndexError):
            return ""
    return value.strip()


def column_index_from_ref(ref: str):
    letters = "".join(ch for ch in str(ref or "") if ch.isalpha())
    if not letters:
        return None
    index = 0
    for char in letters.upper():
        index = index * 26 + (ord(char) - 64)
    return index


def normalize_alias_header(value):
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum() or ch == "_")


def find_header_index(header: list[str], names: set[str]):
    for index, value in enumerate(header):
        if value in names:
            return index
    return None


def cell_at(row: list[str], index: int | None):
    if index is None or index >= len(row):
        return ""
    return str(row[index] or "").strip()


async def import_alias_pattern_rows(guild: discord.Guild | None, rows: list[dict]):
    result = empty_alias_import_result()
    for index, row in enumerate(rows, start=2):
        aliases = participant_tools.extract_manual_names(row.get("aliases") or "")
        if not aliases:
            continue
        target = await resolve_alias_target(guild, row.get("user_id") or "", row.get("username") or "")
        if not target:
            result["unresolved"].append(f"Fila {index}: {row.get('username') or row.get('user_id') or 'sin usuario'}")
            continue
        save_aliases_for_target(target, aliases, result)
    return result


def empty_alias_import_result():
    return {
        "saved": [],
        "replaced": [],
        "unchanged": [],
        "empty": [],
        "unresolved": [],
    }


async def resolve_alias_target(guild: discord.Guild | None, user_id_text: str, username_text: str):
    raw_user = str(user_id_text or "").strip()
    raw_name = str(username_text or "").strip()
    id_match = re.search(r"\d{15,25}", raw_user)
    if id_match:
        user_id = id_match.group(0)
        member = await resolve_guild_member(guild, user_id) if guild else None
        if member:
            return {"user_id": str(member.id), "username": member.display_name}
        scout = next((row for row in get_all_scouts() if str(row[0]) == user_id), None)
        return {"user_id": user_id, "username": scout[1] if scout else (raw_name or user_id)}

    if raw_name and guild:
        found = await participant_tools.resolve_exact_participant(guild, raw_name, set())
        if found:
            return {"user_id": str(found[0]), "username": found[1]}

    if raw_name:
        target = participant_tools.normalize_name(raw_name)
        scout = next((row for row in get_all_scouts() if participant_tools.normalize_name(row[1]) == target), None)
        if scout:
            return {"user_id": str(scout[0]), "username": scout[1]}

    return None


def save_aliases_for_target(target: dict, aliases: list[str], result: dict):
    seen = set()
    for alias in aliases:
        normalized = participant_tools.normalize_name(alias)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        existing = find_scout_alias(alias)
        add_scout_alias(target["user_id"], target["username"], alias)
        item = (target["user_id"], target["username"], alias)
        if existing and str(existing[0]) != str(target["user_id"]):
            result["replaced"].append((*item, existing[1]))
        elif existing:
            result["unchanged"].append(item)
        else:
            result["saved"].append(item)


def build_alias_import_result_embed(result: dict):
    total_saved = len(result["saved"]) + len(result["replaced"]) + len(result["unchanged"])
    embed = discord.Embed(
        title="Importacion de padron",
        description=f"Aliases procesados: **{total_saved}**",
        color=COLOR_SUCCESS if total_saved and not result["unresolved"] else COLOR_WARNING,
    )
    embed.add_field(name="Nuevos", value=str(len(result["saved"])), inline=True)
    embed.add_field(name="Actualizados", value=str(len(result["replaced"])), inline=True)
    embed.add_field(name="Ya existian", value=str(len(result["unchanged"])), inline=True)
    if result["saved"]:
        embed.add_field(name="Guardados", value=format_alias_result_lines(result["saved"]), inline=False)
    if result["replaced"]:
        embed.add_field(name="Reasignados", value=format_alias_replaced_lines(result["replaced"]), inline=False)
    if result["unresolved"]:
        embed.add_field(name="No resueltos", value="\n".join(result["unresolved"][:15])[:1000], inline=False)
    if result["empty"]:
        embed.add_field(
            name="Sin aliases en la fila",
            value=", ".join(f"`{name}`" for name in result["empty"][:20])[:1000],
            inline=False,
        )
    return embed


def format_alias_result_lines(rows: list[tuple]):
    lines = [f"<@{user_id}> - `+{alias}`" for user_id, _, alias in rows[:15]]
    if len(rows) > 15:
        lines.append(f"... y {len(rows) - 15} mas")
    return "\n".join(lines)[:1000]


def format_alias_replaced_lines(rows: list[tuple]):
    lines = [
        f"`+{alias}` -> <@{user_id}> (antes: `{previous}`)"
        for user_id, _, alias, previous in rows[:12]
    ]
    if len(rows) > 12:
        lines.append(f"... y {len(rows) - 12} mas")
    return "\n".join(lines)[:1000]


@admin_group.command(name="export_ranking", description="Exporta el ranking actual o el ultimo cierre semanal")
@app_commands.choices(fuente=RANKING_SOURCE_CHOICES, formato=EXPORT_FORMAT_CHOICES)
async def export_ranking(
    interaction: discord.Interaction,
    fuente: str = "ultimo_cierre",
    formato: str = "xlsx",
):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return

    source = normalize_priority_source(fuente)
    file_format = normalize_export_format(formato)
    source_data = get_ranking_export_source(source)
    if source_data["source"] == "ultimo_cierre" and not source_data["snapshot"]:
        await interaction.response.send_message("Aun no hay cierre semanal guardado para exportar.", ephemeral=True)
        return

    filename = build_ranking_export_filename(source_data, file_format)
    if file_format == "csv":
        file = build_ranking_csv_file(filename, source)
    else:
        file = build_ranking_xlsx_file(filename, source)
    await interaction.response.send_message(
        content=f"Export: **{source_data['label']}**",
        file=file,
        ephemeral=True,
    )


def build_ranking_csv_file(filename: str, source: str = "actual"):
    source_data = get_ranking_export_source(source)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["user_id", "username", *ACTIVIDADES.keys(), "total_puntos", "nivel", "beneficio"])

    for row in get_ranking_export_rows(source_data):
        writer.writerow(row)

    output.seek(0)
    return discord.File(fp=io.BytesIO(output.getvalue().encode("utf-8")), filename=filename)


def build_ranking_xlsx_file(filename: str, source: str = "actual"):
    source_data = get_ranking_export_source(source)
    rows = [
        ["Fuente", source_data["label"]],
        [],
        ["user_id", "username", *ACTIVIDADES.keys(), "total_puntos", "nivel", "beneficio"],
        *get_ranking_export_rows(source_data),
    ]
    return discord.File(fp=io.BytesIO(build_xlsx_bytes(rows)), filename=filename)


def get_ranking_export_source(source: str | None = "actual"):
    source = normalize_priority_source(source)
    if source == "ultimo_cierre":
        snapshot = get_latest_ranking_snapshot()
        if not snapshot:
            return {
                "source": source,
                "label": "Ultimo cierre semanal (sin cierres guardados)",
                "snapshot": None,
                "rows": [],
            }
        return {
            "source": source,
            "label": f"Ultimo cierre semanal #{snapshot[0]} ({snapshot[3]})",
            "snapshot": snapshot,
            "rows": get_ranking_snapshot_rows(snapshot[0]),
        }

    return {
        "source": "actual",
        "label": "Ranking actual",
        "snapshot": None,
        "rows": get_all_scouts(),
    }


def get_ranking_export_rows(source_data: dict):
    exported = []
    for row in source_data["rows"]:
        if source_data["source"] == "ultimo_cierre":
            exported.append(list(row))
            continue
        pts = calc_puntos_totales(row)
        nivel, beneficio = get_nivel(pts)
        exported.append([*row, pts, nivel, beneficio])
    exported.sort(key=lambda item: (-(int(item[7] or 0)), str(item[1]).lower()))
    return exported


def normalize_export_format(value: str | None):
    text = str(value or "xlsx").strip().lower()
    return "csv" if text == "csv" else "xlsx"


def build_ranking_export_filename(source_data: dict, file_format: str):
    if source_data["source"] == "ultimo_cierre" and source_data["snapshot"]:
        snapshot = source_data["snapshot"]
        date_text = safe_export_date(snapshot[3] or snapshot[1])
        return f"ranking_ultimo_cierre_{date_text}.{file_format}"
    date_text = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"ranking_actual_{date_text}.{file_format}"


def safe_export_date(value):
    if not value:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    text = str(value)
    match = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    return match.group(1) if match else datetime.now(timezone.utc).strftime("%Y-%m-%d")


@admin_group.command(name="afks", description="Revisa AFKs por 2 semanas")
async def afks(interaction: discord.Interaction):
    if not (is_admin(interaction) or is_gm_member(interaction.user)):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return

    if not interaction.guild:
        await interaction.response.send_message("Este comando solo funciona dentro del servidor.", ephemeral=True)
        return

    previous_source = get_ranking_export_source("ultimo_cierre")
    if not previous_source["snapshot"]:
        await interaction.response.send_message("Aun no hay cierre semanal guardado para comparar.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    members = await get_non_bot_guild_members(interaction.guild)
    candidates, summary = build_inactive_candidates(members, DEFAULT_INACTIVE_MAX_POINTS)
    embed = build_inactive_review_embed(candidates, summary, DEFAULT_INACTIVE_MAX_POINTS)
    view = InactiveReviewView(candidates, summary, DEFAULT_INACTIVE_MAX_POINTS)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def get_non_bot_guild_members(guild: discord.Guild):
    members_by_id = {
        member.id: member
        for member in guild.members
        if not member.bot
    }
    try:
        async for member in guild.fetch_members(limit=None):
            if not member.bot:
                members_by_id[member.id] = member
    except (discord.Forbidden, discord.HTTPException):
        pass
    return sorted(members_by_id.values(), key=lambda member: member.display_name.lower())


def build_inactive_candidates(
    members: list[discord.Member],
    max_points: int = DEFAULT_INACTIVE_MAX_POINTS,
    limit: int = INACTIVE_REPORT_LIMIT,
):
    current_source = get_ranking_export_source("actual")
    previous_source = get_ranking_export_source("ultimo_cierre")
    current_rows = {
        str(row[0]): ranking_export_row_to_inactive_entry(row)
        for row in get_ranking_export_rows(current_source)
    }
    previous_rows = {
        str(row[0]): ranking_export_row_to_inactive_entry(row)
        for row in get_ranking_export_rows(previous_source)
    }

    candidates = []
    for member in members:
        user_id = str(member.id)
        previous = previous_rows.get(user_id) or {
            "user_id": user_id,
            "username": member.display_name,
            "points": 0,
            "activity_units": 0,
        }
        current = current_rows.get(user_id) or {
            "user_id": user_id,
            "username": member.display_name,
            "points": 0,
            "activity_units": 0,
        }
        if current["points"] > max_points or previous["points"] > max_points:
            continue
        candidates.append({
            "user_id": user_id,
            "username": member.display_name or current["username"] or previous["username"],
            "current_points": current["points"],
            "previous_points": previous["points"],
            "current_units": current["activity_units"],
            "previous_units": previous["activity_units"],
            "two_week_points": current["points"] + previous["points"],
        })

    candidates.sort(
        key=lambda item: (
            item["two_week_points"],
            item["current_points"],
            item["previous_points"],
            str(item["username"]).lower(),
        )
    )
    limited = candidates[:limit]
    summary = {
        "current_label": current_source["label"],
        "previous_label": previous_source["label"],
        "current_total": len(current_rows),
        "previous_total": len(previous_rows),
        "member_total": len(members),
        "candidate_total": len(candidates),
    }
    return limited, summary


def ranking_export_row_to_inactive_entry(row):
    activity_units = 0
    for value in row[2:7]:
        try:
            activity_units += int(value or 0)
        except (TypeError, ValueError):
            continue
    return {
        "user_id": str(row[0]),
        "username": str(row[1] or row[0]),
        "points": int(row[7] or 0),
        "activity_units": activity_units,
    }


def build_inactive_review_embed(
    candidates: list[dict],
    summary: dict,
    max_points: int,
    discarded_count: int = 0,
):
    embed = discord.Embed(
        title="Revision AFK",
        description=(
            f"Criterio activo: **{max_points} pts o menos** en ranking actual y cierre anterior."
        ),
        color=COLOR_WARNING if candidates else COLOR_SUCCESS,
    )
    embed.add_field(name="Detectados", value=f"**{summary['candidate_total']}**", inline=True)
    embed.add_field(name="En revision", value=f"**{len(candidates)}**", inline=True)
    if discarded_count:
        embed.add_field(name="Descartados", value=f"**{discarded_count}**", inline=True)

    if not candidates:
        embed.add_field(
            name="Resultado",
            value="No quedan candidatos en revision.",
            inline=False,
        )
        return embed

    candidate_lines = build_inactive_candidate_lines(candidates)
    for chunk_index, start in enumerate(range(0, len(candidate_lines), 12), start=1):
        chunk = candidate_lines[start:start + 12]
        field_name = "Candidatos" if chunk_index == 1 else f"Candidatos {chunk_index}"
        embed.add_field(name=field_name, value="\n".join(chunk), inline=False)
    embed.set_footer(text="Descarta falsos positivos; luego kickea los restantes.")
    return embed


def build_inactive_candidate_lines(candidates: list[dict]):
    lines = []
    for index, item in enumerate(candidates[:INACTIVE_REPORT_LIMIT], start=1):
        lines.append(format_inactive_candidate_line(index, item))
    return lines


def format_inactive_candidate_line(index: int, item: dict):
    username = clean_inactive_table_name(item.get("username") or item["user_id"], 28)
    return f"`{index:02}` **{username}** - `{item['two_week_points']} pts`"


def clean_inactive_table_name(value, limit: int):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = text.replace("`", "'")
    if not text:
        text = "Sin nombre"
    if len(text) > limit:
        return text[:limit - 1] + "."
    return text


class InactiveReviewView(SafeView):
    def __init__(self, candidates: list[dict], summary: dict, max_points: int):
        super().__init__(timeout=600)
        self.candidates = list(candidates)
        self.summary = dict(summary)
        self.max_points = max_points
        self.discarded_count = 0
        self.refresh_items()

    def refresh_items(self):
        self.clear_items()
        self.add_item(ChangeInactivePointsButton(self))
        if self.candidates:
            self.add_item(KickInactiveButton(self))
            self.add_item(InactiveDiscardSelect(self))

    def current_embed(self):
        return build_inactive_review_embed(
            self.candidates,
            self.summary,
            self.max_points,
            self.discarded_count,
        )


class ChangeInactivePointsButton(discord.ui.Button):
    def __init__(self, review_view: InactiveReviewView):
        super().__init__(
            label="Cambiar puntos",
            style=discord.ButtonStyle.primary,
            row=0,
        )
        self.review_view = review_view

    async def callback(self, interaction: discord.Interaction):
        if not (is_admin(interaction) or is_gm_member(interaction.user)):
            await interaction.response.send_message("No tienes permiso para cambiar este reporte.", ephemeral=True)
            return
        await interaction.response.send_modal(InactivePointsModal(self.review_view))


class KickInactiveButton(discord.ui.Button):
    def __init__(self, review_view: InactiveReviewView):
        super().__init__(
            label="Kickear restantes",
            style=discord.ButtonStyle.danger,
            row=0,
        )
        self.review_view = review_view

    async def callback(self, interaction: discord.Interaction):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Solo el rol GM puede kickear AFKs.", ephemeral=True)
            return
        if not self.review_view.candidates:
            await interaction.response.send_message("No quedan candidatos para kickear.", ephemeral=True)
            return
        await interaction.response.send_modal(InactiveKickConfirmModal(self.review_view))


class InactiveDiscardSelect(discord.ui.Select):
    def __init__(self, review_view: InactiveReviewView):
        self.review_view = review_view
        options = [
            discord.SelectOption(
                label=select_option_label(item, index),
                value=str(item["user_id"]),
                description=f"{item['two_week_points']} pts"[:100],
            )
            for index, item in enumerate(review_view.candidates[:INACTIVE_REPORT_LIMIT], start=1)
        ]
        super().__init__(
            placeholder="Descartar personas del reporte",
            min_values=1,
            max_values=max(1, len(options)),
            options=options,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        if not (is_admin(interaction) or is_gm_member(interaction.user)):
            await interaction.response.send_message("No tienes permiso para descartar candidatos.", ephemeral=True)
            return
        selected_ids = {str(user_id) for user_id in self.values}
        selected = [
            item
            for item in self.review_view.candidates
            if str(item["user_id"]) in selected_ids
        ]
        if not selected:
            await interaction.response.send_message("No encontre candidatos seleccionados.", ephemeral=True)
            return

        self.review_view.candidates = [
            item
            for item in self.review_view.candidates
            if str(item["user_id"]) not in selected_ids
        ]
        self.review_view.discarded_count += len(selected)
        self.review_view.refresh_items()
        await interaction.response.edit_message(
            embed=self.review_view.current_embed(),
            view=self.review_view,
        )


class InactiveKickConfirmModal(SafeModal):
    confirmation = discord.ui.TextInput(label="Confirmacion", placeholder="Escribe KICK", max_length=8)

    def __init__(self, review_view: InactiveReviewView):
        super().__init__(title=f"Kickear {len(review_view.candidates)} AFKs")
        self.review_view = review_view

    async def on_submit(self, interaction: discord.Interaction):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Solo el rol GM puede confirmar kicks.", ephemeral=True)
            return
        if str(self.confirmation.value).strip().upper() != "KICK":
            await interaction.response.send_message("Operacion cancelada. Debes escribir `KICK`.", ephemeral=True)
            return
        if not interaction.guild:
            await interaction.response.send_message("Este boton solo funciona dentro del servidor.", ephemeral=True)
            return

        bot_member = interaction.guild.me
        if not bot_member or not bot_member.guild_permissions.kick_members:
            await interaction.response.send_message("No tengo permiso `Kick Members` en este servidor.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        result = await kick_inactive_candidates(
            interaction.guild,
            interaction.user,
            bot_member,
            list(self.review_view.candidates),
            self.review_view.max_points,
        )
        kicked_ids = {str(member.id) for member in result["kicked"]}
        if kicked_ids:
            self.review_view.candidates = [
                item
                for item in self.review_view.candidates
                if str(item["user_id"]) not in kicked_ids
            ]
            self.review_view.refresh_items()

        await interaction.followup.send(embed=build_inactive_kick_result_embed(result), ephemeral=False)


class InactivePointsModal(SafeModal):
    points = discord.ui.TextInput(
        label="Puntos maximos por semana",
        placeholder="Ej: 0",
        max_length=4,
    )

    def __init__(self, review_view: InactiveReviewView):
        super().__init__(title="Cambiar puntos AFK")
        self.review_view = review_view
        self.points.default = str(review_view.max_points)

    async def on_submit(self, interaction: discord.Interaction):
        if not (is_admin(interaction) or is_gm_member(interaction.user)):
            await interaction.response.send_message("No tienes permiso para cambiar este reporte.", ephemeral=True)
            return
        if not interaction.guild:
            await interaction.response.send_message("Este boton solo funciona dentro del servidor.", ephemeral=True)
            return
        try:
            max_points = int(str(self.points.value).strip())
            if max_points < 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Ingresa un numero valido mayor o igual a 0.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        members = await get_non_bot_guild_members(interaction.guild)
        candidates, summary = build_inactive_candidates(members, max_points)
        self.review_view.candidates = candidates
        self.review_view.summary = summary
        self.review_view.max_points = max_points
        self.review_view.discarded_count = 0
        self.review_view.refresh_items()
        if interaction.message:
            await interaction.followup.edit_message(
                interaction.message.id,
                embed=self.review_view.current_embed(),
                view=self.review_view,
            )
        else:
            await interaction.followup.send(
                embed=self.review_view.current_embed(),
                view=self.review_view,
                ephemeral=True,
            )


def select_option_label(item: dict, index: int):
    username = clean_inactive_table_name(item.get("username") or item["user_id"], 75)
    text = f"#{index} {username} ({item['two_week_points']} pts)"
    return text[:100]


async def kick_inactive_candidates(
    guild: discord.Guild,
    actor: discord.Member,
    bot_member: discord.Member,
    candidates: list[dict],
    max_points: int,
):
    result = {
        "kicked": [],
        "missing": [],
        "skipped": [],
        "errors": [],
    }
    reason = f"RankingBot AFK 2 semanas: <= {max_points} pts en ranking actual y cierre anterior"

    for item in candidates:
        user_id = str(item["user_id"])
        if not is_discord_user_id(user_id):
            result["skipped"].append(f"{item['username']}: ID no valido")
            continue
        member = await resolve_guild_member(guild, user_id)
        if not member:
            result["missing"].append(item)
            continue
        if member.bot:
            result["skipped"].append(f"{member.display_name}: es bot")
            continue
        if member.id == actor.id:
            result["skipped"].append(f"{member.display_name}: no puedes kickearte a ti mismo")
            continue
        if is_gm_member(member) or getattr(member.guild_permissions, "administrator", False):
            result["skipped"].append(f"{member.display_name}: GM/admin protegido")
            continue
        if member.top_role >= bot_member.top_role:
            result["errors"].append(f"No puedo kickear a {member.display_name}: rol igual o superior al mio")
            continue
        try:
            await member.kick(reason=reason)
            result["kicked"].append(member)
        except discord.HTTPException as err:
            result["errors"].append(f"No pude kickear a {member.display_name}: {err}")

    return result


def build_inactive_kick_result_embed(result: dict):
    embed = discord.Embed(
        title="Miembros kickeados",
        color=COLOR_SUCCESS if result["kicked"] and not result["errors"] else COLOR_WARNING,
    )
    embed.add_field(name="Total", value=f"**{len(result['kicked'])}**", inline=True)
    if result["kicked"]:
        embed.add_field(
            name="Lista",
            value=format_kicked_member_lines(result["kicked"]),
            inline=False,
        )
    if result["missing"]:
        embed.add_field(
            name="No encontrados",
            value=", ".join(clean_inactive_table_name(item.get("username") or item["user_id"], 24) for item in result["missing"][:20]),
            inline=False,
        )
    if result["skipped"]:
        embed.add_field(name="Omitidos", value="\n".join(result["skipped"][:10])[:1000], inline=False)
    if result["errors"]:
        embed.add_field(name="Errores", value="\n".join(result["errors"][:10])[:1000], inline=False)
    return embed


def format_kicked_member_lines(members: list[discord.Member]):
    lines = [
        f"`{index:02}` **{clean_inactive_table_name(member.display_name, 28)}**"
        for index, member in enumerate(members[:20], start=1)
    ]
    if len(members) > 20:
        lines.append(f"... y {len(members) - 20} mas")
    return "\n".join(lines)


def build_xlsx_bytes(rows: list[list], sheet_name: str = "Ranking"):
    sheet_name = sanitize_xlsx_sheet_name(sheet_name)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as xlsx:
        xlsx.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>""",
        )
        xlsx.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>""",
        )
        xlsx.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        )
        xlsx.writestr(
            "xl/workbook.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="{xml_escape(sheet_name)}" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
        )
        xlsx.writestr("xl/worksheets/sheet1.xml", build_worksheet_xml(rows))
        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        xlsx.writestr(
            "docProps/core.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:creator>RankingBot</dc:creator><cp:lastModifiedBy>RankingBot</cp:lastModifiedBy>
<dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>
<dcterms:modified xsi:type="dcterms:W3CDTF">{created}</dcterms:modified>
</cp:coreProperties>""",
        )
        xlsx.writestr(
            "docProps/app.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
<Application>RankingBot</Application>
</Properties>""",
        )
    output.seek(0)
    return output.getvalue()


def sanitize_xlsx_sheet_name(name: str):
    cleaned = re.sub(r"[\[\]:*?/\\]", " ", str(name or "Sheet1")).strip()
    return (cleaned or "Sheet1")[:31]


def build_worksheet_xml(rows: list[list]):
    sheet_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(row, start=1):
            cells.append(build_xlsx_cell(column_name(col_index), row_index, value))
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData>'
        "</worksheet>"
    )


def build_xlsx_cell(column: str, row_index: int, value):
    ref = f"{column}{row_index}"
    if value is None:
        value = ""
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"><v>{value}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t>{xml_escape(str(value))}</t></is></c>'


def column_name(index: int):
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def normalize_priority_source(source: str | None):
    text = str(source or "actual").strip().lower().replace("-", "_")
    if text in {"ultimo", "ultimo_cierre", "cierre", "archivo", "snapshot"}:
        return "ultimo_cierre"
    return "actual"


def get_priority_source(source: str | None = "actual"):
    source = normalize_priority_source(source)
    if source == "ultimo_cierre":
        snapshot = get_latest_ranking_snapshot()
        if not snapshot:
            return {
                "source": source,
                "label": "Ultimo cierre semanal (sin cierres guardados)",
                "snapshot": None,
                "rows": [],
            }
        rows = get_ranking_snapshot_rows(snapshot[0])
        return {
            "source": source,
            "label": f"Ultimo cierre semanal #{snapshot[0]} ({snapshot[3]})",
            "snapshot": snapshot,
            "rows": rows,
        }

    return {
        "source": "actual",
        "label": "Ranking actual",
        "snapshot": None,
        "rows": get_all_scouts(),
    }


def row_points_level(row):
    if len(row) >= 10:
        return int(row[7] or 0), row[8], row[9]
    points = calc_puntos_totales(row)
    nivel, beneficio = get_nivel(points)
    return points, nivel, beneficio


def build_priority_candidates(minimo: int, ranking_rows=None):
    candidates = []
    for row in (ranking_rows if ranking_rows is not None else get_all_scouts()):
        points, nivel, beneficio = row_points_level(row)
        if points < minimo:
            continue
        candidates.append({
            "user_id": str(row[0]),
            "username": row[1],
            "points": points,
            "nivel": nivel,
            "beneficio": beneficio,
        })
    candidates.sort(key=lambda item: item["points"], reverse=True)
    return candidates


def build_priority_decision_embed(
    minimo: int,
    candidates: list[dict],
    protected_members: list[discord.Member] | None = None,
    source_label: str = "Ranking actual",
    ranking_rows=None,
):
    protected_members = protected_members or []
    rows = build_priority_export_rows(minimo, protected_members, ranking_rows)
    preview = [
        f"`#{index}` {format_priority_user(item)} - **{item['points']} pts** ({item['nivel']})"
        for index, item in enumerate(rows[:15], start=1)
    ]
    embed = discord.Embed(
        title="Decision semanal de prio",
        description=(
            f"Corte: **{minimo} puntos o mas**\n"
            f"Fuente: **{source_label}**\n"
            f"Califican por ranking: **{len(candidates)}**\n"
            f"Staff/GM protegidos: **{len(protected_members)}**\n"
            f"Total con prio final: **{len(rows)}**\n"
            f"Rol prio: <@&{PRIORITY_ROLE_ID}>"
        ),
        color=COLOR_RANKING,
    )
    embed.add_field(
        name="Vista previa",
        value="\n".join(preview) if preview else "Nadie alcanza el corte.",
        inline=False,
    )
    embed.add_field(
        name="Staff/GM",
        value="Staff y GM mantienen prio aunque no alcancen el corte.",
        inline=False,
    )
    embed.set_footer(text="Vuelve al panel /prio para aplicar o cambiar el corte.")
    return embed


def build_priority_dashboard_embed(guild: discord.Guild, role: discord.Role, minimo: int, source: str = "actual"):
    source_data = get_priority_source(source)
    ranking_rows = source_data["rows"]
    candidates = build_priority_candidates(minimo, ranking_rows)
    protected = get_priority_protected_members(guild)
    rows = build_priority_export_rows(minimo, protected, ranking_rows)
    target_ids = {item["user_id"] for item in rows}
    current_role_members = [member for member in role.members if not member.bot]
    removable = [
        member
        for member in current_role_members
        if str(member.id) not in target_ids and not member_has_any_role(member, PRIORITY_PROTECTED_ROLE_IDS)
    ]
    missing_candidates = [
        item for item in candidates
        if is_discord_user_id(item["user_id"]) and not guild.get_member(int(item["user_id"]))
    ]
    invalid_candidates = [
        item for item in candidates
        if not is_discord_user_id(item["user_id"])
    ]

    preview = [
        f"`#{index}` {format_priority_user(item)} - **{item['points']} pts** ({item['motivo']})"
        for index, item in enumerate(rows[:12], start=1)
    ]

    embed = discord.Embed(
        title="Panel semanal de prio",
        description=(
            f"Rol: {role.mention}\n"
            f"Corte activo: **{minimo} puntos o mas**\n"
            f"Fuente: **{source_data['label']}**"
        ),
        color=COLOR_RANKING,
    )
    embed.add_field(name="Califican por ranking", value=str(len(candidates)), inline=True)
    embed.add_field(name="Staff/GM protegidos", value=str(len(protected)), inline=True)
    embed.add_field(name="Prio final", value=str(len(rows)), inline=True)
    embed.add_field(name="Tienen rol ahora", value=str(len(current_role_members)), inline=True)
    embed.add_field(name="Se agregarian", value=str(count_priority_additions(guild, role, rows)), inline=True)
    embed.add_field(name="Se quitarian", value=str(len(removable)), inline=True)
    embed.add_field(
        name="Vista previa",
        value="\n".join(preview) if preview else "Nadie alcanza el corte y no hay Staff/GM visibles.",
        inline=False,
    )
    if missing_candidates:
        embed.add_field(
            name="Ojo",
            value=f"{len(missing_candidates)} usuarios del ranking no estan en cache; al aplicar se intentara buscarlos.",
            inline=False,
        )
    if invalid_candidates:
        names = ", ".join(f"`{item['username']}`" for item in invalid_candidates[:8])
        embed.add_field(
            name="IDs no validos",
            value=f"{len(invalid_candidates)} registros no tienen ID numerico de Discord: {names}",
            inline=False,
        )
    if source_data["source"] == "ultimo_cierre" and not source_data["snapshot"]:
        embed.add_field(
            name="Sin cierre guardado",
            value="Aun no hay cierres semanales archivados en la base.",
            inline=False,
        )
    embed.set_footer(text="Exporta primero si quieres revisar la lista completa antes de aplicar.")
    return embed


def is_discord_user_id(value):
    return str(value or "").isdigit()


def format_priority_user(item: dict):
    user_id = str(item["user_id"])
    if is_discord_user_id(user_id):
        return f"<@{user_id}>"
    return f"`{item['username']}` (`{user_id}`)"


def count_priority_additions(guild: discord.Guild, role: discord.Role, rows: list[dict]):
    total = 0
    for item in rows:
        member = guild.get_member(int(item["user_id"])) if str(item["user_id"]).isdigit() else None
        if member and role not in member.roles:
            total += 1
    return total


def build_priority_csv_file(minimo: int, guild: discord.Guild | None = None, source: str = "actual"):
    source_data = get_priority_source(source)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["fuente", source_data["label"]])
    writer.writerow(["user_id", "username", "total_puntos", "nivel", "beneficio", "motivo"])
    protected_members = get_priority_protected_members(guild) if guild else []
    for item in build_priority_export_rows(minimo, protected_members, source_data["rows"]):
        writer.writerow([
            item["user_id"],
            item["username"],
            item["points"],
            item["nivel"],
            item["beneficio"],
            item["motivo"],
        ])
    output.seek(0)
    filename = f"prio_{source_data['source']}_corte_{minimo}.csv"
    return discord.File(fp=io.BytesIO(output.getvalue().encode("utf-8")), filename=filename)


def build_priority_export_rows(minimo: int, protected_members: list[discord.Member] | None = None, ranking_rows=None):
    rows = []
    seen = set()
    ranking_rows = ranking_rows if ranking_rows is not None else get_all_scouts()
    for item in build_priority_candidates(minimo, ranking_rows):
        row = dict(item)
        row["motivo"] = "ranking"
        rows.append(row)
        seen.add(row["user_id"])

    for member in protected_members or []:
        user_id = str(member.id)
        if user_id in seen:
            continue
        scout = next((row for row in ranking_rows if str(row[0]) == user_id), None)
        if scout:
            points, nivel, beneficio = row_points_level(scout)
        else:
            points = 0
            nivel, beneficio = get_nivel(0)
        rows.append({
            "user_id": user_id,
            "username": member.display_name,
            "points": points,
            "nivel": nivel,
            "beneficio": beneficio,
            "motivo": "staff_gm",
        })
        seen.add(user_id)

    rows.sort(key=lambda item: (item["motivo"] != "ranking", -item["points"], item["username"].lower()))
    return rows


async def sync_priority_role(guild: discord.Guild, role: discord.Role, minimo: int, source: str = "actual"):
    source_data = get_priority_source(source)
    candidates = build_priority_candidates(minimo, source_data["rows"])
    eligible_ids = {item["user_id"] for item in candidates}
    protected_ids = {str(member.id) for member in get_priority_protected_members(guild)}
    target_ids = eligible_ids | protected_ids

    result = {
        "source_label": source_data["label"],
        "candidates": candidates,
        "assigned": [],
        "already": [],
        "removed": [],
        "kept_protected": [],
        "missing": [],
        "errors": [],
    }

    for user_id in sorted(target_ids):
        member = await resolve_guild_member(guild, user_id)
        if not member:
            result["missing"].append(user_id)
            continue
        is_protected = str(user_id) in protected_ids
        if role in member.roles:
            bucket = "kept_protected" if is_protected and user_id not in eligible_ids else "already"
            result[bucket].append(member)
            continue
        try:
            await member.add_roles(role, reason=f"Ranking prio semanal ({source_data['source']}): {minimo}+ puntos")
            result["assigned"].append(member)
        except discord.HTTPException as err:
            result["errors"].append(f"No pude dar prio a {member.display_name}: {err}")

    for member in list(role.members):
        user_id = str(member.id)
        if user_id in target_ids or member_has_any_role(member, PRIORITY_PROTECTED_ROLE_IDS):
            continue
        try:
            await member.remove_roles(role, reason=f"Ranking prio semanal ({source_data['source']}): debajo de {minimo} puntos")
            result["removed"].append(member)
        except discord.HTTPException as err:
            result["errors"].append(f"No pude quitar prio a {member.display_name}: {err}")

    return result


async def resolve_guild_member(guild: discord.Guild, user_id: str):
    member = guild.get_member(int(user_id)) if str(user_id).isdigit() else None
    if member:
        return member
    try:
        return await guild.fetch_member(int(user_id))
    except (discord.HTTPException, ValueError):
        return None


def member_has_any_role(member: discord.Member, role_ids: set[str]):
    member_role_ids = {str(role.id) for role in getattr(member, "roles", [])}
    return bool(member_role_ids & {str(role_id) for role_id in role_ids})


def get_priority_protected_members(guild: discord.Guild | None):
    if not guild:
        return []
    members_by_id = {
        str(member.id): member
        for member in guild.members
        if not member.bot and member_has_any_role(member, PRIORITY_PROTECTED_ROLE_IDS)
    }
    for role_id in PRIORITY_PROTECTED_ROLE_IDS:
        role = guild.get_role(int(role_id))
        if not role:
            continue
        for member in role.members:
            if not member.bot:
                members_by_id[str(member.id)] = member
    return sorted(members_by_id.values(), key=lambda member: member.display_name.lower())


def format_priority_member_changes(members: list[discord.Member], empty_text: str = "Nadie."):
    if not members:
        return empty_text
    sorted_members = sorted(members, key=lambda member: member.display_name.lower())
    lines = [f"{member.mention} - `{member.display_name}`" for member in sorted_members[:20]]
    if len(sorted_members) > 20:
        lines.append(f"... y {len(sorted_members) - 20} mas")
    return "\n".join(lines)[:1000]


def build_priority_apply_embed(minimo: int, role: discord.Role, result: dict):
    embed = discord.Embed(
        title="Prio semanal sincronizada",
        description=(
            f"Corte aplicado: **{minimo} puntos o mas**\n"
            f"Fuente: **{result.get('source_label', 'Ranking actual')}**\n"
            f"Rol: {role.mention}\n"
            f"Califican por ranking: **{len(result['candidates'])}**"
        ),
        color=COLOR_SUCCESS if not result["errors"] else COLOR_WARNING,
    )
    embed.add_field(name="Rol agregado", value=str(len(result["assigned"])), inline=True)
    embed.add_field(name="Ya tenian prio", value=str(len(result["already"])), inline=True)
    embed.add_field(name="Prio quitada", value=str(len(result["removed"])), inline=True)
    embed.add_field(name="Staff/GM protegidos", value=str(len(result["kept_protected"])), inline=True)
    embed.add_field(
        name=f"Anadidos ({len(result['assigned'])})",
        value=format_priority_member_changes(result["assigned"]),
        inline=False,
    )
    embed.add_field(
        name=f"Quitados ({len(result['removed'])})",
        value=format_priority_member_changes(result["removed"]),
        inline=False,
    )
    if result["missing"]:
        embed.add_field(
            name="No encontrados",
            value=", ".join(f"`{user_id}`" for user_id in result["missing"][:15]),
            inline=False,
        )
    if result["errors"]:
        embed.add_field(name="Errores", value="\n".join(result["errors"][:8])[:1000], inline=False)
    return embed


class PrioDashboardView(SafeView):
    def __init__(self, minimo: int = DEFAULT_PRIORITY_MIN_POINTS, source: str = "actual"):
        super().__init__(timeout=600)
        self.minimo = max(0, int(minimo or DEFAULT_PRIORITY_MIN_POINTS))
        self.source = normalize_priority_source(source)

    @discord.ui.button(label="Actualizar", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        role = get_priority_role(interaction)
        if not role:
            await interaction.response.send_message(f"No encontre el rol prio `{PRIORITY_ROLE_ID}`.", ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=build_priority_dashboard_embed(interaction.guild, role, self.minimo, self.source),
            view=self,
        )

    @discord.ui.button(label="Cambiar corte", style=discord.ButtonStyle.primary)
    async def change_cutoff(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        await interaction.response.send_modal(PrioCutoffModal(self.minimo, self.source))

    @discord.ui.button(label="Exportar CSV", style=discord.ButtonStyle.secondary)
    async def export_csv(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        if not interaction.guild:
            await interaction.response.send_message("Este boton solo funciona dentro del servidor.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        source_data = get_priority_source(self.source)
        candidates = build_priority_candidates(self.minimo, source_data["rows"])
        protected = get_priority_protected_members(interaction.guild)
        embed = build_priority_decision_embed(
            self.minimo,
            candidates,
            protected,
            source_data["label"],
            source_data["rows"],
        )
        file = build_priority_csv_file(self.minimo, interaction.guild, self.source)
        await interaction.followup.send(embed=embed, file=file, ephemeral=True)

    @discord.ui.button(label="Ver caps", style=discord.ButtonStyle.secondary)
    async def caps(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=build_priority_caps_embed(), ephemeral=True)

    @discord.ui.button(label="Aplicar roles", style=discord.ButtonStyle.danger)
    async def apply_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        await interaction.response.send_modal(PrioApplyConfirmModal(self.minimo, self.source))


class PrioCutoffModal(SafeModal):
    value = discord.ui.TextInput(label="Corte de puntos", placeholder="Ej: 50", max_length=4)

    def __init__(self, current_minimo: int, source: str = "actual"):
        super().__init__(title="Cambiar corte de prio")
        self.source = normalize_priority_source(source)
        self.value.default = str(current_minimo)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        try:
            minimo = int(str(self.value.value).strip())
            if minimo < 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Ingresa un numero valido mayor o igual a 0.", ephemeral=True)
            return

        role = get_priority_role(interaction)
        if not role:
            await interaction.response.send_message(f"No encontre el rol prio `{PRIORITY_ROLE_ID}`.", ephemeral=True)
            return

        await interaction.response.edit_message(
            embed=build_priority_dashboard_embed(interaction.guild, role, minimo, self.source),
            view=PrioDashboardView(minimo, self.source),
        )


class PrioApplyConfirmModal(SafeModal):
    confirmation = discord.ui.TextInput(label="Confirmacion", placeholder="Escribe APLICAR", max_length=10)

    def __init__(self, minimo: int, source: str = "actual"):
        super().__init__(title=f"Aplicar prio {minimo}+ pts")
        self.minimo = minimo
        self.source = normalize_priority_source(source)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        if str(self.confirmation.value).strip() != "APLICAR":
            await interaction.response.send_message("Operacion cancelada. Debes escribir `APLICAR`.", ephemeral=True)
            return
        if not interaction.guild:
            await interaction.response.send_message("Este boton solo funciona dentro del servidor.", ephemeral=True)
            return

        role = get_priority_role(interaction)
        if not role:
            await interaction.response.send_message(f"No encontre el rol prio `{PRIORITY_ROLE_ID}`.", ephemeral=True)
            return

        bot_member = interaction.guild.me
        if bot_member and role >= bot_member.top_role:
            await interaction.response.send_message(
                "No puedo administrar ese rol porque esta por encima o al mismo nivel que mi rol.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await sync_priority_role(interaction.guild, role, self.minimo, self.source)
        embed = build_priority_apply_embed(self.minimo, role, result)
        file = build_priority_csv_file(self.minimo, interaction.guild, self.source)
        await interaction.followup.send(embed=embed, file=file, ephemeral=True)


def get_priority_role(interaction: discord.Interaction):
    if not interaction.guild:
        return None
    return interaction.guild.get_role(PRIORITY_ROLE_ID)


def archive_current_weekly_ranking(reason: str):
    return create_ranking_snapshot(
        current_weekly_ranking_start(),
        datetime.now(timezone.utc),
        reason,
    )


@admin_group.command(name="reset_ranking", description="Resetea todos los puntos del ranking")
async def reset_ranking(interaction: discord.Interaction, confirmacion: str):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return

    if confirmacion != "RESET":
        await interaction.response.send_message("Escribe `RESET` en confirmacion para resetear el ranking.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    snapshot_id = archive_current_weekly_ranking("manual_reset")
    reset_all()
    await publish_or_update_dashboard()
    await publish_or_update_info_ranking()
    snapshot_text = f" Cierre guardado: `#{snapshot_id}`." if snapshot_id else " No habia puntos para archivar."
    await send_log(f"Ranking reseteado por {interaction.user.mention}.{snapshot_text}")
    await interaction.followup.send(f"Ranking reseteado.{snapshot_text}", ephemeral=True)


async def send_log(text: str):
    if not LOG_CHANNEL_ID:
        return

    channel = bot.get_channel(LOG_CHANNEL_ID)
    if not channel:
        channel = await bot.fetch_channel(LOG_CHANNEL_ID)

    await channel.send(text)


async def send_weekly_ranking_export(snapshot_id: int | None = None):
    if not WEEKLY_EXPORT_CHANNEL_ID:
        return

    channel = bot.get_channel(WEEKLY_EXPORT_CHANNEL_ID)
    if not channel:
        channel = await bot.fetch_channel(WEEKLY_EXPORT_CHANNEL_ID)

    source = "ultimo_cierre" if snapshot_id else "actual"
    source_data = get_ranking_export_source(source)
    filename = build_ranking_export_filename(source_data, "xlsx")
    snapshot_text = f" Cierre guardado: `#{snapshot_id}`." if snapshot_id else ""
    await channel.send(
        content=f"Export semanal del ranking antes del reset.{snapshot_text}",
        file=build_ranking_xlsx_file(filename, source),
    )


async def weekly_reset_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        target = next_weekly_reset_at()
        wait_seconds = max(1, (target - datetime.now(timezone.utc)).total_seconds())
        print(f"[RESET] Proximo reset ranking: {target.isoformat()}")
        await asyncio.sleep(wait_seconds)
        try:
            snapshot_id = archive_current_weekly_ranking("weekly_reset")
            await send_weekly_ranking_export(snapshot_id)
            reset_all()
            await publish_or_update_dashboard()
            await publish_or_update_info_ranking()
            snapshot_text = f" Cierre guardado: `#{snapshot_id}`." if snapshot_id else " No habia puntos para archivar."
            await send_log(f"Reset semanal del ranking ejecutado automaticamente.{snapshot_text}")
        except Exception as err:
            print(f"[RESET ERROR] {err}")


def next_weekly_reset_at():
    now = datetime.now(timezone.utc)
    target = now.replace(
        hour=AUTO_RESET_HOUR_UTC,
        minute=AUTO_RESET_MINUTE_UTC,
        second=0,
        microsecond=0,
    )
    days_ahead = (AUTO_RESET_WEEKDAY_UTC - target.weekday()) % 7
    target = target + timedelta(days=days_ahead)
    if target <= now:
        target = target + timedelta(days=7)
    return target


def current_weekly_ranking_start():
    now = datetime.now(timezone.utc)
    target = now.replace(
        hour=AUTO_RESET_HOUR_UTC,
        minute=AUTO_RESET_MINUTE_UTC,
        second=0,
        microsecond=0,
    )
    days_back = (target.weekday() - AUTO_RESET_WEEKDAY_UTC) % 7
    target = target - timedelta(days=days_back)
    if target > now:
        target = target - timedelta(days=7)
    return target


tree.add_command(admin_group)


if __name__ == "__main__":
    bot.run(TOKEN)
