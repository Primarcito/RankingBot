import os
import csv
import io
import asyncio
import traceback
import re
from datetime import datetime, timedelta, timezone
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from database import (
    add_activity,
    add_evidence_participants,
    add_scout_alias,
    calc_puntos_totales,
    create_evidence_review,
    get_evidence_by_thread,
    get_evidence_participants as db_get_evidence_participants,
    get_evidence_review_message_id,
    get_all_scouts,
    get_bot_state,
    get_puntos,
    get_nivel,
    get_pending_evidence_message_ids,
    get_scout_aliases,
    init_db,
    remove_scout_alias,
    reset_all,
    set_bot_state,
    set_evidence_thread,
    set_evidence_review_message,
    subtract_activity,
)
from config import ACTIVIDADES, APPLICATION_ID, COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING, \
    DASHBOARD_CHANNEL_ID, GUILD_ID, INFO_RANKING_CHANNEL_ID, \
    WEEKLY_EXPORT_CHANNEL_ID, \
    EVIDENCE_CATEGORY, EVIDENCE_CATEGORY_ID, EVIDENCE_CATEGORY_IDS, EVIDENCE_CHANNEL_IDS, \
    EVIDENCE_CHANNELS, EVIDENCE_REVIEW_CHANNEL_ID, IMAGE_EXTENSIONS, LOG_CHANNEL_ID, \
    AUTO_RESET_ENABLED, AUTO_RESET_HOUR_UTC, AUTO_RESET_MINUTE_UTC, AUTO_RESET_WEEKDAY_UTC
from views import (
    DashboardView,
    EvidenceAuthorConfirmView,
    EvidenceReviewView,
    EvidenceReviewerSuggestionConfirmView,
    InfoRankingView,
    build_participant_resolution_embed,
    refresh_review_participants,
)
from embeds import build_dashboard_embed, build_info_ranking_embed, build_perfil_embed
from permissions import can_review_member, is_admin
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

# Opciones de actividad para los slash commands
ACT_CHOICES = [
    app_commands.Choice(name=meta["label"], value=key)
    for key, meta in ACTIVIDADES.items()
]

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
    try:
        await message.add_reaction("\N{HOURGLASS}")
    except discord.HTTPException:
        pass

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
            value=", ".join(f"+{name}" for name in unresolved_names)[:1000],
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

def extract_scouteo_summary_records(message: discord.Message):
    text = get_embed_search_text(message)
    if not text:
        return []

    normalized = text.lower()
    if "resumen del dia" not in normalized and "resumen del día" not in normalized:
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

    hours_per_point, maps_per_point = get_scouteo_count_settings()
    records = calculate_scouteo_records(records, hours_per_point, maps_per_point)
    return await create_scouteo_count_review(message, records, hours_per_point, maps_per_point)

async def create_scouteo_count_review(
    message: discord.Message,
    records: list[dict],
    hours_per_point: int,
    maps_per_point: int,
):
    participant_rows = []
    unresolved_names = []
    suggested_participants = []
    excluded_user_ids = set()
    for record in records:
        if record["total"] <= 0:
            continue
        participants, unresolved, suggestions = await participant_tools.resolve_names(
            message.guild,
            [record["name"]],
            excluded_user_ids,
        )
        if participants:
            user_id, display_name = participants[0]
            participant_rows.append((user_id, display_name, record["total"], record))
            excluded_user_ids.add(str(user_id))
        else:
            unresolved_names.extend(unresolved)
            suggested_participants.extend(suggestions)

    if not participant_rows:
        print("[SCOUTEO SUMMARY] ignorado: sin participantes con puntos calculados")
        return False

    owner_id, owner_name, _, _ = participant_rows[0]
    pts = create_evidence_review(
        str(message.id),
        owner_id,
        owner_name,
        "scouteo",
        participant_rows
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
            f"Reglas: `{hours_per_point}h = 1 punto | {maps_per_point} mapas = 1 punto`\n"
            f"Total calculado: `{sum(row[2] for row in participant_rows) * pts}` pts\n"
            f"Canal: {message.channel.mention}\n"
            f"[Abrir evidencia]({message.jump_url})"
        )
    )
    participant_text = "\n".join(
        (
            f"<@{user_id}> - `{cantidad * pts}` pts "
            f"({record['hours']}h {record['minutes']}m, {record['maps']} mapas; "
            f"{record['hour_points']} por horas + {record['map_points']} por mapas)"
        )
        for user_id, _, cantidad, record in participant_rows[:20]
    )
    embed.add_field(name="Participantes", value=participant_text[:1000], inline=False)
    if unresolved_names:
        embed.add_field(
            name="No resueltos",
            value=", ".join(f"+{name}" for name in unresolved_names)[:1000],
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
            "Reconoci algunos nombres escritos con `+`. "
            "Marca solo las personas correctas y confirma para agregarlas a la evidencia."
        )
    else:
        title = "Participantes sin reconocer"
        description = (
            "No pude asociar estos nombres a una cuenta. "
            "Corrigelos en este hilo con menciones, IDs o `+nombres`."
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
            value=", ".join(f"+{name}" for name in unresolved_names)[:1000],
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
                "Escribe aqui menciones, IDs o nombres con `+`. "
                "Ejemplo: `+violeth +chino +littleponny`."
            ),
            inline=False,
        )

        view = None
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


@tree.command(name="conteo", description="Calcula scouteo desde un resumen diario por ID de mensaje")
@app_commands.describe(id_mensaje="ID del mensaje del resumen de Mapas en este canal")
async def conteo(interaction: discord.Interaction, id_mensaje: str):
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

    hours_per_point, maps_per_point = get_scouteo_count_settings()
    embed = build_scouteo_count_embed(
        source_message,
        calculate_scouteo_records(records, hours_per_point, maps_per_point),
        hours_per_point,
        maps_per_point,
    )
    await interaction.response.send_message(
        embed=embed,
        view=ScouteoCountView(source_message, records, hours_per_point, maps_per_point),
    )


def build_scouteo_count_embed(
    source_message: discord.Message,
    records: list[dict],
    hours_per_point: int,
    maps_per_point: int,
):
    unit_points = get_puntos("scouteo")
    total_units = sum(record["total"] for record in records)
    embed = discord.Embed(
        title="Conteo de scouteo",
        description=(
            f"Mensaje: [abrir resumen]({source_message.jump_url})\n"
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


class ScouteoCountView(discord.ui.View):
    def __init__(
        self,
        source_message: discord.Message,
        records: list[dict],
        hours_per_point: int,
        maps_per_point: int,
    ):
        super().__init__(timeout=300)
        self.source_message = source_message
        self.records = records
        self.hours_per_point = hours_per_point
        self.maps_per_point = maps_per_point

    def calculated_records(self):
        return calculate_scouteo_records(self.records, self.hours_per_point, self.maps_per_point)

    async def refresh(self, interaction: discord.Interaction):
        embed = build_scouteo_count_embed(
            self.source_message,
            self.calculated_records(),
            self.hours_per_point,
            self.maps_per_point,
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
            ),
            view=self,
        )


class ScouteoCountRuleModal(discord.ui.Modal):
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


@tree.command(name="analizar_mapeo", description="Analiza logs de mapeo desde el inicio semanal del ranking")
async def analizar_mapeo(interaction: discord.Interaction):
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

    week_start = current_weekly_ranking_start()
    analysis_start = get_mapeo_analysis_start(week_start)
    events = []
    scanned = 0
    latest_event_at = None
    async for message in channel.history(limit=None, after=analysis_start):
        scanned += 1
        event = mapping_analysis.parse_mapping_message(message)
        if event:
            await enrich_mapping_event_player(message.guild, event)
            events.append(event)
            if not latest_event_at or message.created_at > latest_event_at:
                latest_event_at = message.created_at

    if not events:
        await interaction.followup.send(
            f"No encontre eventos validos de mapeo desde `{analysis_start.strftime('%Y-%m-%d %H:%M UTC')}`."
        )
        return

    analysis = mapping_analysis.analyze_mapping_events(events)
    await interaction.followup.send(
        embed=build_mapeo_analysis_embed(analysis, scanned, analysis_start),
        files=build_mapeo_analysis_files(analysis),
        view=MapeoAnalysisView(analysis, scanned, analysis_start, latest_event_at),
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


def build_mapeo_analysis_embed(analysis: dict, scanned: int, analysis_start: datetime, confirmed_by=None):
    summary = analysis["summary"]
    embed = discord.Embed(
        title="Analisis de mapeo",
        description=(
            f"Canal: <#{MAPEO_LOG_CHANNEL_ID}>\n"
            f"Desde: `{analysis_start.strftime('%Y-%m-%d %H:%M UTC')}`\n"
            f"Mensajes revisados: `{scanned}`\n"
            f"Eventos detectados: `{summary['total_events']}`\n"
            f"Reglas: `Road unica +1 | Priority +0.15 | RELOCK +0.15 | Duplicada +0`"
        ),
        color=COLOR_SUCCESS if confirmed_by else COLOR_WARNING,
    )
    embed.add_field(
        name="Resumen general",
        value=(
            f"Rutas agregadas: `{summary['road_total']}`\n"
            f"Rutas unicas: `{summary['road_unique']}`\n"
            f"Duplicados: `{summary['road_duplicates']}`\n"
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
            "Las repeticiones quedan en duplicados y suman `0` puntos."
        ),
        inline=False,
    )
    embed.add_field(
        name="Ranking",
        value=mapping_analysis.build_ranking_table(analysis["ranking"]),
        inline=False,
    )
    embed.add_field(
        name="Rutas duplicadas",
        value=mapping_analysis.build_duplicates_table(analysis["duplicates"]),
        inline=False,
    )
    if confirmed_by:
        embed.add_field(name="Estado", value=f"Enviado a revision por {confirmed_by.mention}", inline=False)
    return embed


def build_mapeo_analysis_files(analysis: dict):
    return [
        discord.File(mapping_analysis.ranking_csv_bytes(analysis["ranking"]), filename="mapeo_ranking.csv"),
        discord.File(mapping_analysis.duplicates_csv_bytes(analysis["duplicates"]), filename="mapeo_duplicados.csv"),
        discord.File(mapping_analysis.events_csv_bytes(analysis["events"]), filename="mapeo_eventos.csv"),
    ]


class MapeoAnalysisView(discord.ui.View):
    def __init__(self, analysis: dict, scanned: int, analysis_start: datetime, latest_event_at: datetime | None):
        super().__init__(timeout=1800)
        self.analysis = analysis
        self.scanned = scanned
        self.analysis_start = analysis_start
        self.latest_event_at = latest_event_at

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

        embed = build_mapeo_analysis_embed(self.analysis, self.scanned, self.analysis_start, interaction.user)
        await review_channel.send(embed=embed, files=build_mapeo_analysis_files(self.analysis))

        if self.latest_event_at:
            set_bot_state(MAPEO_ANALYSIS_CHECKPOINT_KEY, self.latest_event_at.isoformat())

        for item in self.children:
            item.disabled = True
        await interaction.message.edit(
            content="Analisis enviado a administrar evidencias. Este rango ya no se contara en el proximo analisis.",
            embed=embed,
            view=self,
        )


@tree.command(name="reset_analisis_mapeo", description="Reinicia el checkpoint semanal del analisis de mapeo")
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


@tree.command(name="dashboard_scouts", description="Publica o actualiza el dashboard de scouts")
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


@tree.command(name="info_ranking", description="Publica la guía y ranking general")
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


@tree.command(name="modificar_puntos", description="Suma o resta actividades a un scout")
@app_commands.choices(actividad=ACT_CHOICES)
async def modificar_puntos(
    interaction: discord.Interaction,
    usuario: discord.Member,
    actividad: app_commands.Choice[str],
    cantidad: int,
):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return

    if cantidad == 0:
        await interaction.response.send_message("Cantidad no puede ser 0.", ephemeral=True)
        return

    actividad_key = actividad.value
    if cantidad > 0:
        pts = add_activity(str(usuario.id), usuario.display_name, actividad_key, cantidad)
        signo = "+"
    else:
        pts = subtract_activity(str(usuario.id), usuario.display_name, actividad_key, abs(cantidad))
        signo = "-"

    meta = ACTIVIDADES[actividad_key]
    embed = discord.Embed(
        description=(
            f"{meta['emoji']} **{meta['label']}**\n"
            f"Usuario: {usuario.mention}\n"
            f"Cantidad: `{cantidad}`\n"
            f"Puntos: `{signo}{pts}`"
        ),
        color=COLOR_SUCCESS if cantidad > 0 else COLOR_ERROR,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="registrar_alt", description="Asocia uno o varios nombres alternos a un scout")
@app_commands.describe(usuario="Scout principal que recibira los puntos", nombres="Ej: +littleponny, +otroNombre")
async def registrar_alt(interaction: discord.Interaction, usuario: discord.Member, nombres: str):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return

    aliases = participant_tools.extract_manual_names(nombres)
    if not aliases:
        await interaction.response.send_message("Escribe al menos un nombre valido.", ephemeral=True)
        return

    saved = []
    for alias in aliases:
        if add_scout_alias(str(usuario.id), usuario.display_name, alias):
            saved.append(alias)

    embed = discord.Embed(
        title="Aliases registrados",
        description=(
            f"Main: {usuario.mention}\n"
            f"Nombres: {', '.join(f'`+{alias}`' for alias in saved)}"
        ),
        color=COLOR_SUCCESS,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="quitar_alt", description="Quita un nombre alterno del ranking")
@app_commands.describe(nombre="Nombre alterno a quitar, con o sin +")
async def quitar_alt(interaction: discord.Interaction, nombre: str):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return

    removed = remove_scout_alias(nombre)
    message = f"`+{nombre.lstrip('+')}` eliminado." if removed else "No encontre ese alias."
    await interaction.response.send_message(message, ephemeral=True)


@tree.command(name="ver_alts", description="Muestra los nombres alternos asociados a un scout")
async def ver_alts(interaction: discord.Interaction, usuario: discord.Member):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return

    rows = get_scout_aliases(str(usuario.id))
    if not rows:
        await interaction.response.send_message(f"{usuario.mention} no tiene aliases registrados.", ephemeral=True)
        return

    aliases = ", ".join(f"`+{alias}`" for _, _, alias in rows)
    await interaction.response.send_message(f"Aliases de {usuario.mention}: {aliases}", ephemeral=True)


@tree.command(name="export_ranking", description="Exporta el ranking como CSV")
async def export_ranking(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return

    file = build_ranking_csv_file("ranking_scouts.csv")
    await interaction.response.send_message(file=file, ephemeral=True)


def build_ranking_csv_file(filename: str):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["user_id", "username", *ACTIVIDADES.keys(), "total_puntos", "nivel", "beneficio"])

    for row in get_all_scouts():
        pts = calc_puntos_totales(row)
        nivel, beneficio = get_nivel(pts)
        writer.writerow([*row, pts, nivel, beneficio])

    output.seek(0)
    return discord.File(fp=io.BytesIO(output.getvalue().encode("utf-8")), filename=filename)


@tree.command(name="reset_ranking", description="Resetea todos los puntos del ranking")
async def reset_ranking(interaction: discord.Interaction, confirmacion: str):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return

    if confirmacion != "RESET":
        await interaction.response.send_message("Escribe `RESET` en confirmacion para resetear el ranking.", ephemeral=True)
        return

    reset_all()
    await publish_or_update_dashboard()
    await publish_or_update_info_ranking()
    await send_log(f"Ranking reseteado por {interaction.user.mention}.")
    await interaction.response.send_message("Ranking reseteado.", ephemeral=True)


async def send_log(text: str):
    if not LOG_CHANNEL_ID:
        return

    channel = bot.get_channel(LOG_CHANNEL_ID)
    if not channel:
        channel = await bot.fetch_channel(LOG_CHANNEL_ID)

    await channel.send(text)


async def send_weekly_ranking_export():
    if not WEEKLY_EXPORT_CHANNEL_ID:
        return

    channel = bot.get_channel(WEEKLY_EXPORT_CHANNEL_ID)
    if not channel:
        channel = await bot.fetch_channel(WEEKLY_EXPORT_CHANNEL_ID)

    filename = f"ranking_scouts_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.csv"
    await channel.send(
        content="Export semanal del ranking antes del reset.",
        file=build_ranking_csv_file(filename),
    )


async def weekly_reset_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        target = next_weekly_reset_at()
        wait_seconds = max(1, (target - datetime.now(timezone.utc)).total_seconds())
        print(f"[RESET] Proximo reset ranking: {target.isoformat()}")
        await asyncio.sleep(wait_seconds)
        try:
            await send_weekly_ranking_export()
            reset_all()
            await publish_or_update_dashboard()
            await publish_or_update_info_ranking()
            await send_log("Reset semanal del ranking ejecutado automaticamente.")
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


if __name__ == "__main__":
    bot.run(TOKEN)
