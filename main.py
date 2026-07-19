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

load_dotenv()

from database import (
    add_activity,
    add_evidence_participants,
    add_scout_alias,
    adjust_snapshot_activity,
    calc_puntos_totales,
    create_ranking_snapshot,
    create_evidence_review,
    get_audit_events,
    get_pending_audit_dm_events,
    get_evidence_by_thread,
    get_evidence_summary,
    get_evidence_participants as db_get_evidence_participants,
    get_evidence_review_message_id,
    get_all_scouts,
    get_all_config,
    get_bot_state,
    get_scouteo_projection,
    get_latest_ranking_snapshot,
    get_puntos,
    get_pending_evidence_message_ids,
    get_overdue_pending_evidence,
    get_pending_count,
    get_priority_min_points,
    get_prio_status,
    get_recent_evidence,
    get_scout,
    get_scout_aliases,
    find_scout_alias,
    get_ranking_snapshot,
    get_ranking_snapshot_for_time,
    get_ranking_snapshot_rows,
    init_db,
    move_evidence_to_snapshot,
    mark_evidence_review_alerted,
    mark_audit_dm_notified,
    remove_scout_alias,
    record_audit_event,
    reset_all,
    set_bot_state,
    set_scouteo_contributions,
    set_evidence_thread,
    set_evidence_review_message,
    set_puntos,
    set_priority_min_points,
    subtract_activity,
)
from config import ACTIVIDADES, APPLICATION_ID, COLOR_PANEL, COLOR_PERFIL, COLOR_RANKING, COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING, \
    DASHBOARD_CHANNEL_ID, GUILD_ID, INFO_RANKING_CHANNEL_ID, \
    WEEKLY_EXPORT_CHANNEL_ID, \
    EVIDENCE_CATEGORY, EVIDENCE_CATEGORY_ID, EVIDENCE_CATEGORY_IDS, EVIDENCE_CHANNEL_IDS, \
    AUDIT_DM_USER_ID, EVIDENCE_CHANNELS, EVIDENCE_REVIEW_CHANNEL_ID, IMAGE_EXTENSIONS, LOG_CHANNEL_ID, \
    AUTO_RESET_ENABLED, AUTO_RESET_HOUR_UTC, AUTO_RESET_MINUTE_UTC, AUTO_RESET_WEEKDAY_UTC, \
    DEFAULT_PRIORITY_MIN_POINTS, GM_ROLE_IDS, PRIORITY_PROTECTED_ROLE_IDS, PRIORITY_ROLE_ID, \
    REVIEWER_ROLE_IDS
from views import (
    DashboardView,
    EvidenceAuthorConfirmView,
    EvidenceThreadParticipantView,
    EvidenceReviewView,
    EvidenceReviewerSuggestionConfirmView,
    InfoRankingView,
    PointsSelectView,
    ResetView,
    build_participant_resolution_embed,
    refresh_review_participants,
)
from embeds import (
    build_dashboard_embed,
    build_info_ranking_embed,
    build_perfil_embed,
    build_priority_requirement_embed,
)
from permissions import (
    AccessLevel,
    can_review_member,
    get_access_level,
    is_admin,
    is_gm_member,
)
from emojis import button_emoji, reaction_emoji, reaction_variants, text_emoji
from audit_log import (
    audit_action_label,
    build_audit_markdown,
    format_audit_dm_line,
    is_audit_dm_relevant,
)
from ocr import improve_confidence_for_channel, is_ineligible_ocr, read_message_ocr, suggest_activity_from_ocr
import participants as participant_tools
import mapping_analysis
from scouteo_scoring import (
    calculate_scouteo_records,
    calculate_scouteo_points,
    format_scouteo_summary,
    parse_multiplier_hundredths,
)

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
PENDING_ALERT_TASK_STARTED = False
AUDIT_DM_TASK_STARTED = False
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
MAPEO_SCALING_EXPONENT = 1.0
BOT_BUILD = "mapeo-lineal-v5"
PRIO_POST_CHANNEL_ID = 1505949944043929651
PRIO_SCORE_PAGE_SIZE = 10
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


def record_interaction_audit(
    interaction: discord.Interaction,
    category: str,
    action: str,
    *,
    target_type: str | None = None,
    target_id: str | int | None = None,
    summary: str = "",
    details: dict | None = None,
):
    user = interaction.user
    return record_audit_event(
        category,
        action,
        actor_id=str(user.id),
        actor_name=getattr(user, "display_name", None) or getattr(user, "name", None) or str(user),
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        summary=summary,
        details=details,
    )


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
    global PENDING_ALERT_TASK_STARTED, AUDIT_DM_TASK_STARTED
    init_db()
    migrate_scouteo_accumulation_settings()
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

    if not PENDING_ALERT_TASK_STARTED:
        bot.loop.create_task(pending_evidence_alert_loop())
        PENDING_ALERT_TASK_STARTED = True

    if not AUDIT_DM_TASK_STARTED:
        bot.loop.create_task(audit_dm_notification_loop())
        AUDIT_DM_TASK_STARTED = True

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
        title=f"{text_emoji('AUDIT')} Analizando evidencia",
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
        await analyzing_msg.edit(
            embed=discord.Embed(
                title=f"{text_emoji('AUDIT')} Evidencia registrada",
                description="Este mensaje ya tiene una revisión.",
                color=COLOR_PANEL,
            )
        )
        asyncio.create_task(delete_message_after(analyzing_msg))
        return
    record_audit_event(
        "evidencias",
        "crear_revision",
        actor_id=str(message.author.id),
        actor_name=message.author.display_name,
        target_type="evidencia",
        target_id=str(message.id),
        summary=f"Envio {actividad} a revision con {len(participants) or 1} participante(s).",
        details={
            "actividad": actividad,
            "participantes": len(participants) or 1,
            "canal_id": str(message.channel.id),
        },
    )

    review_channel = await get_review_channel(message)
    embed = discord.Embed(
        title=f"{text_emoji('PENDING')} Evidencia pendiente",
        color=COLOR_WARNING,
        description=(
            f"{message.author.mention} · **{ACTIVIDADES[actividad]['label']}** · `{pts} pts`\n"
            f"OCR: **{ocr_confidence}** · {message.channel.mention}\n"
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
    await refresh_public_messages_from_message(message)
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
        title=f"{text_emoji('APPROVED')} Evidencia enviada a revisión",
        description=done_description,
        color=COLOR_SUCCESS
    )
    await analyzing_msg.edit(embed=done_embed)
    asyncio.create_task(delete_message_after(analyzing_msg))


async def delete_message_after(message: discord.Message, delay: int = 60):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except discord.HTTPException:
        pass


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


def get_pending_alert_role(guild: discord.Guild):
    reviewer_roles = [
        guild.get_role(int(role_id))
        for role_id in REVIEWER_ROLE_IDS
        if str(role_id).isdigit()
    ]
    reviewer_roles = [role for role in reviewer_roles if role is not None]
    officer_roles = [
        role for role in reviewer_roles
        if "officer" in role.name.casefold()
    ]
    if officer_roles:
        return sorted(officer_roles, key=lambda role: role.position, reverse=True)[0]

    non_gm_roles = [
        role for role in reviewer_roles
        if str(role.id) not in GM_ROLE_IDS
    ]
    if non_gm_roles:
        return sorted(non_gm_roles, key=lambda role: role.position, reverse=True)[0]

    gm_roles = [
        guild.get_role(int(role_id))
        for role_id in GM_ROLE_IDS
        if str(role_id).isdigit()
    ]
    gm_roles = [role for role in gm_roles if role is not None]
    return sorted(gm_roles, key=lambda role: role.position, reverse=True)[0] if gm_roles else None


def build_pending_evidence_alert_content(
    role_mention: str,
    activity_label: str,
    created_at: str,
    review_url: str,
):
    created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    timestamp = int(created.timestamp())
    return (
        f"{role_mention} **{activity_label}** pendiente "
        f"desde <t:{timestamp}:R>. [Abrir revisión]({review_url})"
    )


async def alert_overdue_pending_evidence():
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    role = get_pending_alert_role(guild)
    if not role:
        print("[PENDING ALERT] No encontre un rol Officer o GM configurado.")
        return

    try:
        channel = (
            bot.get_channel(EVIDENCE_REVIEW_CHANNEL_ID)
            or await bot.fetch_channel(EVIDENCE_REVIEW_CHANNEL_ID)
        )
    except discord.HTTPException as err:
        print(f"[PENDING ALERT] No pude abrir el canal de revision: {err}")
        return

    for item in get_overdue_pending_evidence(hours=5):
        evidence = get_evidence_summary(item["message_id"])
        if not evidence or evidence[4] != "pending":
            continue
        activity_label = ACTIVIDADES.get(
            item["activity"],
            {"label": item["activity"] or "Evidencia"},
        )["label"]
        review_url = (
            f"https://discord.com/channels/{guild.id}/{channel.id}/"
            f"{item['review_message_id']}"
        )
        content = build_pending_evidence_alert_content(
            role.mention,
            activity_label,
            item["created_at"],
            review_url,
        )
        try:
            try:
                review_message = await channel.fetch_message(
                    int(item["review_message_id"])
                )
                await review_message.reply(
                    content,
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions(
                        roles=True,
                        users=False,
                        everyone=False,
                    ),
                )
            except discord.NotFound:
                await channel.send(
                    content,
                    allowed_mentions=discord.AllowedMentions(
                        roles=True,
                        users=False,
                        everyone=False,
                    ),
                )
            if mark_evidence_review_alerted(item["message_id"]):
                record_audit_event(
                    "evidencias",
                    "alerta_revision_pendiente",
                    actor_name="Sistema",
                    target_type="evidencia",
                    target_id=item["message_id"],
                    summary=(
                        f"Etiqueto a {role.name} porque la revision de "
                        f"{activity_label} supero 5 horas pendiente."
                    ),
                    details={
                        "rol_id": str(role.id),
                        "actividad": item["activity"],
                        "creada": item["created_at"],
                    },
                )
        except (discord.Forbidden, discord.HTTPException) as err:
            print(f"[PENDING ALERT] {item['message_id']}: {err}")


async def pending_evidence_alert_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await alert_overdue_pending_evidence()
        except Exception as err:
            print(f"[PENDING ALERT ERROR] {err}")
        await asyncio.sleep(300)


async def send_pending_audit_dms():
    if not str(AUDIT_DM_USER_ID).isdigit():
        print("[AUDIT DM] El ID configurado no es valido.")
        return
    try:
        recipient = (
            bot.get_user(int(AUDIT_DM_USER_ID))
            or await bot.fetch_user(int(AUDIT_DM_USER_ID))
        )
    except discord.HTTPException as err:
        print(f"[AUDIT DM] No pude encontrar al destinatario: {err}")
        return

    for event in get_pending_audit_dm_events(limit=25):
        if not is_audit_dm_relevant(event):
            mark_audit_dm_notified(event["id"])
            continue
        try:
            await recipient.send(
                format_audit_dm_line(event),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            mark_audit_dm_notified(event["id"])
        except (discord.Forbidden, discord.HTTPException) as err:
            print(f"[AUDIT DM] Evento {event['id']}: {err}")
            break


async def audit_dm_notification_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await send_pending_audit_dms()
        except Exception as err:
            print(f"[AUDIT DM ERROR] {err}")
        await asyncio.sleep(10)


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
    old_reactions = (
        reaction_variants("APPROVED")
        + reaction_variants("REJECTED")
        + reaction_variants("PENDING")
        + ["\N{OUTBOX TRAY}"]
    )
    for emoji in old_reactions:
        await remove_bot_reaction(message, emoji)
    try:
        await message.add_reaction(reaction_emoji("PENDING"))
    except discord.HTTPException:
        pass

async def remove_bot_reaction(message: discord.Message, emoji):
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
SCOUTEO_HOURS_PER_POINT = 4
SCOUTEO_MAPS_PER_POINT = 3
SCOUTEO_HOURS_SETTING_KEY = "scouteo_count_hours_per_point"
SCOUTEO_MAPS_SETTING_KEY = "scouteo_count_maps_per_point"
SCOUTEO_DASHBOARD_STATE_PREFIX = "scouteo_count_dashboard:"
SCOUTEO_ACCUMULATION_MIGRATION_KEY = "scouteo_accumulation_v1"

def migrate_scouteo_accumulation_settings():
    if get_bot_state(SCOUTEO_ACCUMULATION_MIGRATION_KEY):
        return
    set_bot_state(SCOUTEO_HOURS_SETTING_KEY, str(SCOUTEO_HOURS_PER_POINT))
    if not get_bot_state(SCOUTEO_MAPS_SETTING_KEY):
        set_bot_state(SCOUTEO_MAPS_SETTING_KEY, str(SCOUTEO_MAPS_PER_POINT))
    set_bot_state(SCOUTEO_ACCUMULATION_MIGRATION_KEY, datetime.now(timezone.utc).isoformat())

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
        multiplier_hundredths = parse_multiplier_hundredths(line[time_match.end():])
        records.append({
            "name": name,
            "hours": hours,
            "minutes": minutes,
            "maps": maps,
            "multiplier_hundredths": multiplier_hundredths,
        })
    return records

def combine_scouteo_records(records: list[dict]):
    if not records:
        return {
            "name": "",
            "hours": 0,
            "minutes": 0,
            "maps": 0,
            "hour_points": 0,
            "map_points": 0,
            "base_total": 0,
            "multiplier_hundredths": 100,
            "total": 0,
            "calculated_points": 0,
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
        "base_total": sum(record.get("base_total", record["total"]) for record in records),
        "multiplier_hundredths": min(record.get("multiplier_hundredths", 100) for record in records),
        "total": sum(record["total"] for record in records),
        "calculated_points": sum(record.get("calculated_points", 0) for record in records),
        "source_names": source_names,
    }

def format_unresolved_scouteo_records(records: list[dict], unit_points: int):
    lines = []
    for record in records[:20]:
        lines.append(
            f"`{record['name']}` — "
            f"{format_scouteo_summary(record, record['total'], unit_points, record.get('calculated_points'))}"
        )
    if len(records) > 20:
        lines.append(f"... y {len(records) - 20} mas")
    return "\n".join(lines)

def format_scouteo_participant_line(user_id: str, cantidad: int, record: dict, unit_points: int, points: int):
    source_names = record.get("source_names") or [record["name"]]
    source_text = ""
    if len(source_names) > 1:
        source_text = f"; nombres: {', '.join(source_names[:4])}"
        if len(source_names) > 4:
            source_text += f" +{len(source_names) - 4}"

    if source_text:
        source_text = source_text.replace("; nombres: ", " · alias: ")
    balance_text = ""
    if "accumulated_minutes" in record:
        total_minutes = int(record["accumulated_minutes"])
        balance_text = (
            f" · acumulado: {total_minutes // 60}h{total_minutes % 60:02d}m"
            f" / {int(record.get('accumulated_maps', 0))} mapas"
        )
    return f"<@{user_id}> — {format_scouteo_summary(record, cantidad, unit_points, points)}{balance_text}{source_text}"

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

    try:
        records = extract_scouteo_summary_records(message)
    except ValueError as err:
        print(f"[SCOUTEO SUMMARY] ignorado: {err}")
        return False
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
    unit_points = get_puntos("scouteo")
    participant_rows_by_user = {}
    unresolved_records = []
    suggested_participants = []
    for record in records:
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
                    "records": [],
                }
            participant_rows_by_user[user_id]["display_name"] = display_name
            participant_rows_by_user[user_id]["records"].append(record)
        else:
            unresolved_records.extend(dict(record, name=unresolved_name) for unresolved_name in unresolved)
            suggested_participants.extend(suggestions)

    participant_rows = []
    contributions = []
    for item in participant_rows_by_user.values():
        combined = combine_scouteo_records(item["records"])
        incoming_minutes = sum((int(record["hours"]) * 60) + int(record["minutes"]) for record in item["records"])
        incoming_maps = sum(int(record["maps"]) for record in item["records"])
        projection = get_scouteo_projection(
            item["user_id"], incoming_minutes, incoming_maps,
            hours_per_point, maps_per_point, target_snapshot_id,
        )
        units = projection["units"]
        multiplier = min(int(record.get("multiplier_hundredths", 100)) for record in item["records"])
        points = calculate_scouteo_points(units, unit_points, multiplier)
        combined.update({
            "total": units,
            "base_total": units,
            "hour_points": projection["hour_units"],
            "map_points": projection["map_units"],
            "calculated_points": points,
            "multiplier_hundredths": multiplier,
            "accumulated_minutes": projection["total_minutes"],
            "accumulated_maps": projection["total_maps"],
        })
        participant_rows.append((item["user_id"], item["display_name"], units, combined, points))
        contributions.append((
            item["user_id"], incoming_minutes, incoming_maps,
            hours_per_point, maps_per_point, multiplier,
        ))
    unresolved_names = [record["name"] for record in unresolved_records]

    if not participant_rows:
        print("[SCOUTEO SUMMARY] ignorado: sin participantes resueltos")
        return False

    owner_id, owner_name, _, _, _ = participant_rows[0]
    pts = create_evidence_review(
        str(message.id),
        owner_id,
        owner_name,
        "scouteo",
        [(user_id, name, units, points) for user_id, name, units, _, points in participant_rows],
        target_snapshot_id=target_snapshot_id,
    )
    if pts <= 0:
        print("[SCOUTEO SUMMARY] duplicado")
        return False
    set_scouteo_contributions(str(message.id), contributions)
    record_audit_event(
        "evidencias",
        "crear_revision_scouteo",
        actor_id=str(message.author.id),
        actor_name=message.author.display_name,
        target_type="evidencia",
        target_id=str(message.id),
        summary=(
            f"Creo una revision de scouteo para {len(participant_rows)} participante(s), "
            f"destino {target_label}."
        ),
        details={
            "participantes": len(participant_rows),
            "horas_minimas": hours_per_point,
            "mapas_por_unidad": maps_per_point,
            "destino_cierre": target_snapshot_id,
        },
    )

    review_channel = await get_review_channel(message)
    embed = discord.Embed(
        title=f"{text_emoji('PENDING')} Evidencia pendiente",
        color=COLOR_WARNING,
        description=(
            f"**{ACTIVIDADES['scouteo']['label']}** · Resumen del Día → **{target_label}**\n"
            f"`{hours_per_point}h = 1u · {maps_per_point} mapas = 1u` · "
            f"**{sum(row[4] for row in participant_rows)} pts**\n"
            f"{message.channel.mention} · [Abrir evidencia]({message.jump_url})"
        )
    )
    participant_text = "\n".join(
        format_scouteo_participant_line(user_id, cantidad, record, pts, points)
        for user_id, _, cantidad, record, points in participant_rows[:20]
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
        record_audit_event(
            "participantes",
            "agregar_a_evidencia",
            actor_id=str(message.author.id),
            actor_name=message.author.display_name,
            target_type="evidencia",
            target_id=evidence_message_id,
            summary=f"Agrego {len(participants)} participante(s) desde el hilo de evidencia.",
            details={
                "origen": "hilo_de_evidencia",
                "participantes": ", ".join(f"{name} ({user_id})" for user_id, name, *_ in participants),
            },
        )
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

    try:
        records = extract_scouteo_summary_records(source_message)
    except ValueError as err:
        await interaction.response.send_message(str(err), ephemeral=True)
        return
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
    if not is_gm_member(interaction.user):
        await interaction.response.send_message("Esta accion requiere jerarquia GM / Lider.", ephemeral=True)
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

    record_interaction_audit(
        interaction,
        "cierres",
        "mover_conteo",
        target_type="evidencia",
        target_id=message_id,
        summary=f"Movio el conteo al cierre #{snapshot[0]} ({result.get('status')}).",
        details={
            "cierre_id": snapshot[0],
            "actividad": result.get("activity"),
            "estado": result.get("status"),
            "participantes": len(result.get("participants") or []),
        },
    )
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
        title=f"{text_emoji('REJECTED')} No pude mover el conteo",
        description=messages.get(reason, f"Estado no soportado: `{reason or result.get('status')}`"),
        color=COLOR_ERROR,
    )
    embed.add_field(name="ID mensaje", value=f"`{message_id}`", inline=False)
    return embed


def build_move_count_result_embed(message_id: str, snapshot, result: dict):
    if result["status"] == "pending_retargeted":
        embed = discord.Embed(
            title=f"{text_emoji('CALENDAR')} Conteo redirigido al cierre",
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
        title=f"{text_emoji('CALENDAR')} Conteo movido al cierre semanal",
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
    total_points = sum(
        calculate_scouteo_points(
            record["total"],
            unit_points,
            record.get("multiplier_hundredths", 100),
        )
        for record in records
    )
    embed = discord.Embed(
        title=f"{text_emoji('SCOUT')} Conteo de Scouteo",
        description=(
            f"Mensaje: [abrir resumen]({source_message.jump_url})\n"
            f"Destino: **{target_label}**\n"
            f"Reglas: `{hours_per_point}h = 1 unidad | {maps_per_point} mapas = 1 unidad`\n"
            f"Valor Scouteo: `{unit_points}` pts por unidad\n"
            f"Total: `{total_points}` pts"
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
        "Scout        Tiempo  Map Hrs Mps  x   Ud Pts",
        "------------ ------- --- --- --- ---- -- ---",
    ]
    for record in records[:12]:
        name = record["name"][:12].ljust(12)
        time_text = f"{record['hours']}h{record['minutes']:02d}m".rjust(7)
        maps = str(record["maps"]).rjust(3)
        hour_points = str(record["hour_points"]).rjust(3)
        map_points = str(record["map_points"]).rjust(3)
        multiplier = f"{record.get('multiplier_hundredths', 100) / 100:.2f}".rjust(4)
        total = str(record["total"]).rjust(2)
        points = str(calculate_scouteo_points(
            record["total"], unit_points, record.get("multiplier_hundredths", 100)
        )).rjust(3)
        lines.append(f"{name} {time_text} {maps} {hour_points} {map_points} {multiplier} {total} {points}")

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

    @discord.ui.button(
        label="Horas",
        emoji=button_emoji("PENDING"),
        style=discord.ButtonStyle.secondary,
    )
    async def change_hours(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_review_member(interaction.user):
            await interaction.response.send_message("No tienes permiso para editar este conteo.", ephemeral=True)
            return
        await interaction.response.send_modal(ScouteoCountRuleModal(self, "hours"))

    @discord.ui.button(
        label="Mapas",
        emoji=button_emoji("MAP"),
        style=discord.ButtonStyle.secondary,
    )
    async def change_maps(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_review_member(interaction.user):
            await interaction.response.send_message("No tienes permiso para editar este conteo.", ephemeral=True)
            return
        await interaction.response.send_modal(ScouteoCountRuleModal(self, "maps"))

    @discord.ui.button(
        label="Revisión",
        emoji=button_emoji("EVIDENCE"),
        style=discord.ButtonStyle.success,
    )
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

        record_interaction_audit(
            interaction,
            "evidencias",
            "preparar_conteo_scouteo",
            target_type="evidencia",
            target_id=self.source_message.id,
            summary=(
                f"Preparo el conteo de scouteo para {self.target_label} "
                f"con regla {self.hours_per_point}h / {self.maps_per_point} mapas."
            ),
            details={
                "destino_cierre": self.target_snapshot_id,
                "horas_minimas": self.hours_per_point,
                "mapas_por_unidad": self.maps_per_point,
            },
        )
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
        title = "Horas para 1 unidad" if target == "hours" else "Mapas para 1 unidad"
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

        previous = (
            self.view_ref.hours_per_point
            if self.target == "hours"
            else self.view_ref.maps_per_point
        )
        if self.target == "hours":
            self.view_ref.hours_per_point = amount
        else:
            self.view_ref.maps_per_point = amount
        record_interaction_audit(
            interaction,
            "scouteo",
            "cambiar_regla_conteo",
            target_type="evidencia",
            target_id=self.view_ref.source_message.id,
            summary=(
                f"Cambio {'horas minimas' if self.target == 'hours' else 'mapas por unidad'} "
                f"de {previous} a {amount}."
            ),
            details={"regla": self.target, "antes": previous, "despues": amount},
        )
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
        checkpoint_text = " Checkpoint aprobado usado." if target.get("checkpoint_source") else ""
        await interaction.followup.send(
            f"No encontre eventos validos de mapeo desde `{analysis_start.strftime('%Y-%m-%d %H:%M UTC')}`{range_end}."
            f"{fallback_text}{checkpoint_text} Build `{BOT_BUILD}`."
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
            checkpoint_source=target.get("checkpoint_source"),
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
            target.get("checkpoint_key"),
            target.get("checkpoint_source"),
            target.get("range_fallback", False),
        ),
    )


async def enrich_mapping_event_player(guild: discord.Guild, event):
    if not event.discord_id:
        return

    user_id = int(event.discord_id)
    member = guild.get_member(user_id) if guild else None
    if not member and guild:
        try:
            member = await guild.fetch_member(user_id)
        except (discord.HTTPException, ValueError):
            member = None

    if member and not member.bot:
        event.player = member.display_name
        return

    user = bot.get_user(user_id)
    if not user:
        try:
            user = await bot.fetch_user(user_id)
        except (discord.HTTPException, ValueError):
            user = None
    if user and not user.bot:
        event.player = getattr(user, "global_name", None) or user.name


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
    if not analysis_end:
        analysis_end = datetime.now(timezone.utc)
    analysis_start = analysis_end - timedelta(days=7)
    stored_start = parse_snapshot_datetime(snapshot[2])
    used_fallback = not stored_start or abs((stored_start - analysis_start).total_seconds()) > 60
    return analysis_start, analysis_end, used_fallback


def get_mapeo_checkpoint_key(target_snapshot_id: int | None = None):
    if target_snapshot_id:
        return f"{MAPEO_ANALYSIS_CHECKPOINT_KEY}:cierre:{target_snapshot_id}"
    return MAPEO_ANALYSIS_CHECKPOINT_KEY


def get_mapeo_checkpoint_start(
    checkpoint_key: str,
    fallback_start: datetime,
    analysis_end: datetime | None = None,
    fallback_keys: tuple[str, ...] = (),
):
    checked_keys = (checkpoint_key, *fallback_keys)
    for key in checked_keys:
        checkpoint_at = parse_snapshot_datetime(get_bot_state(key))
        if not checkpoint_at:
            continue
        if checkpoint_at < fallback_start:
            continue
        if analysis_end and checkpoint_at > analysis_end:
            continue
        return checkpoint_at, key
    return fallback_start, None


def get_mapeo_count_target(source: str = "actual"):
    source = normalize_priority_source(source)
    if source == "ultimo_cierre":
        snapshot = get_latest_ranking_snapshot()
        if not snapshot:
            return {"missing": True}

        analysis_start, analysis_end, range_fallback = normalize_mapeo_snapshot_range(snapshot)
        checkpoint_key = get_mapeo_checkpoint_key(int(snapshot[0]))
        analysis_start, checkpoint_source = get_mapeo_checkpoint_start(
            checkpoint_key,
            analysis_start,
            analysis_end,
            fallback_keys=(MAPEO_ANALYSIS_CHECKPOINT_KEY,),
        )
        return {
            "snapshot_id": int(snapshot[0]),
            "label": f"Cierre semanal #{snapshot[0]} ({snapshot[3]})",
            "analysis_start": analysis_start,
            "analysis_end": analysis_end,
            "checkpoint_key": checkpoint_key,
            "checkpoint_source": checkpoint_source,
            "range_fallback": range_fallback,
            "missing": False,
        }

    week_start = current_weekly_ranking_start()
    checkpoint_key = get_mapeo_checkpoint_key()
    analysis_start, checkpoint_source = get_mapeo_checkpoint_start(checkpoint_key, week_start)
    return {
        "snapshot_id": None,
        "label": "Ranking actual",
        "analysis_start": analysis_start,
        "analysis_end": None,
        "checkpoint_key": checkpoint_key,
        "checkpoint_source": checkpoint_source,
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
    checkpoint_source: str | None = None,
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
        title=f"{text_emoji('MAP')} Análisis de Mapeo",
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
    if checkpoint_source:
        embed.add_field(
            name="Checkpoint aprobado",
            value="El analisis empieza desde el ultimo mapeo aprobado para este destino.",
            inline=False,
        )
    if range_fallback:
        embed.add_field(
            name="Rango corregido",
            value="Use automaticamente los 7 dias anteriores al fin del cierre, sin confiar en el inicio guardado.",
            inline=False,
        )
    embed.add_field(
        name="Parametros",
        value=(
            f"Valor Mapeo: `{mapeo_value}` pt por unidad\n"
            f"Tope mejor aporte: `{max_units}` unidades x `{mapeo_value}` pt Mapeo = `{max_units * mapeo_value}` pts\n"
            f"Pesos: `Road {mapping_analysis.format_score(road_weight)} | Priority {mapping_analysis.format_score(priority_weight)} | RELOCK {mapping_analysis.format_score(relock_weight)}`\n"
            f"Curva: `proporcional lineal`\n"
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
            "Duplicados suman `0`. El mejor aporte recibe el tope de unidades; los aportes menores se calculan proporcionalmente al peso del top y el bot multiplica esas unidades por el valor de Mapeo."
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
        checkpoint_key: str | None = None,
        checkpoint_source: str | None = None,
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
        self.checkpoint_key = checkpoint_key or get_mapeo_checkpoint_key(target_snapshot_id)
        self.checkpoint_source = checkpoint_source
        self.range_fallback = range_fallback

    @discord.ui.button(
        label="Revisión",
        emoji=button_emoji("EVIDENCE"),
        style=discord.ButtonStyle.success,
    )
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
            checkpoint_source=self.checkpoint_source,
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
            self.checkpoint_key,
            self.checkpoint_source,
            self.range_fallback,
        )
        review_message = await review_channel.send(embed=embed, view=review_view)
        review_view.message = review_message
        record_interaction_audit(
            interaction,
            "mapeo",
            "enviar_a_revision",
            target_type="mensaje_revision",
            target_id=review_message.id,
            summary=f"Envio el analisis de mapeo a revision para {self.target_label}.",
            details={
                "eventos_escaneados": self.scanned,
                "jugadores": len(self.analysis.get("ranking") or []),
                "destino_cierre": self.target_snapshot_id,
            },
        )

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
                checkpoint_source=self.checkpoint_source,
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
        checkpoint_key: str | None = None,
        checkpoint_source: str | None = None,
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
        self.checkpoint_key = checkpoint_key or get_mapeo_checkpoint_key(target_snapshot_id)
        self.checkpoint_source = checkpoint_source
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
            checkpoint_source=self.checkpoint_source,
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

    @discord.ui.button(
        label="Tope",
        emoji=button_emoji("SETTINGS"),
        style=discord.ButtonStyle.secondary,
    )
    async def change_units(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_review_member(interaction.user):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        await interaction.response.send_modal(MapeoMaxUnitsModal(self))

    @discord.ui.button(
        label="Valor",
        emoji=button_emoji("POINTS"),
        style=discord.ButtonStyle.secondary,
    )
    async def change_mapeo_value(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_review_member(interaction.user):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        await interaction.response.send_modal(MapeoActivityValueModal(self))

    @discord.ui.button(
        label="Pesos",
        emoji=button_emoji("MULTIPLIER"),
        style=discord.ButtonStyle.secondary,
    )
    async def change_weights(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_review_member(interaction.user):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        await interaction.response.send_modal(MapeoScoringModal(self))

    @discord.ui.button(
        label="Aprobar",
        emoji=button_emoji("APPROVED"),
        style=discord.ButtonStyle.success,
    )
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

        if self.latest_event_at:
            set_bot_state(self.checkpoint_key, self.latest_event_at.isoformat())

        record_interaction_audit(
            interaction,
            "mapeo",
            "aprobar_analisis",
            target_type="cierre" if self.target_snapshot_id else "ranking_actual",
            target_id=self.target_snapshot_id,
            summary=(
                f"Aplico {applied_units_total} unidades y {total_points} puntos de mapeo "
                f"a {len(awards) - len(failed)} jugador(es)."
            ),
            details={
                "destino": self.target_label,
                "unidades": applied_units_total,
                "puntos": total_points,
                "jugadores": len(awards) - len(failed),
                "omitidos_sin_id": len(skipped),
                "fallidos": len(failed),
            },
        )
        for item in self.children:
            item.disabled = True
        skipped_text = f" No aplicados sin ID: {', '.join(skipped[:5])}." if skipped else ""
        failed_text = f" No aplicados: {', '.join(failed[:5])}." if failed else ""
        await interaction.response.edit_message(
            embed=self.embed(
                f"Aprobado por {interaction.user.mention}. "
                f"Destino: **{self.target_label}**. "
                f"Aplicadas `{applied_units_total}` unidades de mapeo x `{mapeo_value}` pt = `{total_points}` pts a `{len(awards) - len(failed)}` jugadores. "
                f"Checkpoint actualizado para futuros analisis. "
                f"{'Rango cerrado para futuros analisis.' if not self.target_snapshot_id else 'El ranking actual no fue modificado.'}{skipped_text}{failed_text}",
                COLOR_SUCCESS,
            ),
            view=self,
        )
        if not self.target_snapshot_id:
            await publish_or_update_dashboard()
            await publish_or_update_info_ranking()

    @discord.ui.button(
        label="Rechazar",
        emoji=button_emoji("REJECTED"),
        style=discord.ButtonStyle.danger,
    )
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_review_member(interaction.user):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return

        record_interaction_audit(
            interaction,
            "mapeo",
            "rechazar_analisis",
            target_type="cierre" if self.target_snapshot_id else "ranking_actual",
            target_id=self.target_snapshot_id,
            summary=f"Rechazo el analisis de mapeo para {self.target_label}; el rango quedo abierto.",
        )
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

        previous = self.review_view.max_units
        self.review_view.max_units = max_units
        record_interaction_audit(
            interaction,
            "mapeo",
            "cambiar_tope",
            target_type="revision_mapeo",
            summary=f"Cambio el tope de unidades de {previous} a {max_units}.",
            details={"antes": previous, "despues": max_units},
        )
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
        super().__init__(title="Cambiar valor de Mapeo")
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

        previous = get_puntos("mapeo") or 0
        set_puntos("mapeo", mapeo_value)
        record_interaction_audit(
            interaction,
            "configuracion",
            "cambiar_valor_actividad",
            target_type="actividad",
            target_id="mapeo",
            summary=f"Mapeo: {previous} -> {mapeo_value} puntos por unidad.",
            details={"antes": previous, "despues": mapeo_value},
        )
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

        previous = {
            "road": self.review_view.road_weight,
            "priority": self.review_view.priority_weight,
            "relock": self.review_view.relock_weight,
        }
        self.review_view.road_weight = road_weight
        self.review_view.priority_weight = priority_weight
        self.review_view.relock_weight = relock_weight
        record_interaction_audit(
            interaction,
            "mapeo",
            "cambiar_pesos",
            target_type="revision_mapeo",
            summary=(
                f"Pesos Road/Priority/RELOCK: "
                f"{previous['road']}/{previous['priority']}/{previous['relock']} -> "
                f"{road_weight}/{priority_weight}/{relock_weight}."
            ),
            details={
                "road_antes": previous["road"],
                "priority_antes": previous["priority"],
                "relock_antes": previous["relock"],
                "road_despues": road_weight,
                "priority_despues": priority_weight,
                "relock_despues": relock_weight,
            },
        )
        await interaction.response.defer(ephemeral=True)
        if self.review_view.message:
            await self.review_view.message.edit(
                embed=self.review_view.embed(f"Pesos ajustados por {interaction.user.mention}."),
                view=self.review_view,
            )
        await interaction.followup.send("Pesos de mapeo actualizados.", ephemeral=True)


@admin_group.command(name="reset_analisis", description="Reinicia el checkpoint semanal del analisis de mapeo")
async def reset_analisis_mapeo(interaction: discord.Interaction):
    if not is_gm_member(interaction.user):
        await interaction.response.send_message("Esta accion requiere jerarquia GM / Lider.", ephemeral=True)
        return

    week_start = current_weekly_ranking_start()
    set_bot_state(MAPEO_ANALYSIS_CHECKPOINT_KEY, week_start.isoformat())
    record_interaction_audit(
        interaction,
        "mapeo",
        "reiniciar_checkpoint",
        target_type="checkpoint",
        target_id=MAPEO_ANALYSIS_CHECKPOINT_KEY,
        summary=f"Reinicio el checkpoint a {week_start.strftime('%Y-%m-%d %H:%M UTC')}.",
    )
    await interaction.response.send_message(
        f"Checkpoint de mapeo reiniciado a `{week_start.strftime('%Y-%m-%d %H:%M UTC')}`.",
        ephemeral=True,
    )


@admin_group.command(name="dashboard_scouts", description="Publica o actualiza el dashboard de scouts")
async def dashboard_scouts(interaction: discord.Interaction):
    if not is_gm_member(interaction.user):
        await interaction.response.send_message("Esta accion requiere jerarquia GM / Lider.", ephemeral=True)
        return

    await publish_or_update_dashboard()
    record_interaction_audit(
        interaction,
        "publicaciones",
        "actualizar_dashboard",
        target_type="canal",
        target_id=DASHBOARD_CHANNEL_ID,
        summary="Actualizo el dashboard publico de scouts.",
    )
    await interaction.response.send_message("Dashboard actualizado.", ephemeral=True)


async def publish_or_update_dashboard():
    channel = bot.get_channel(DASHBOARD_CHANNEL_ID)
    if not channel:
        channel = await bot.fetch_channel(DASHBOARD_CHANNEL_ID)

    embed = build_dashboard_embed()
    dashboard_msg = None
    async for msg in channel.history(limit=20):
        title = msg.embeds[0].title if msg.embeds else ""
        if msg.author.id == bot.user.id and (
            "Ranking Semanal" in (title or "") or
            "Salon del Ranking" in (title or "") or
            "Dashboard Scouts" in (title or "")
        ):
            dashboard_msg = msg
            break

    if dashboard_msg:
        await dashboard_msg.edit(embed=embed, view=DashboardView())
    else:
        await channel.send(embed=embed, view=DashboardView())

# ── Run ───────────────────────────────────────────────────────────────────────

async def mi_ranking(interaction: discord.Interaction):
    embed = build_perfil_embed(str(interaction.user.id), interaction.user.display_name)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="ranking", description="Muestra tu perfil, el ranking y el requisito de prio")
async def ranking_hub(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=build_ranking_dashboard_embed(interaction.user),
        view=RankingDashboardView(),
        ephemeral=True,
    )


@tree.command(name="conteo", description="Abre el conteo de todas las actividades")
async def counting_hub(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "Requiere jerarquia Officer / Admin.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        embed=build_counting_dashboard_embed(),
        view=CountingDashboardView(),
        ephemeral=True,
    )


@tree.command(name="admin", description="Abre la gestion de RankingBot segun tu jerarquia")
async def administration_hub(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "Requiere jerarquia Officer / Admin.",
            ephemeral=True,
        )
        return
    level = get_access_level(interaction.user)
    await interaction.response.send_message(
        embed=build_admin_dashboard_embed(level),
        view=AdminDashboardView(level),
        ephemeral=True,
    )


def build_ranking_dashboard_embed(member):
    cutoff = get_priority_min_points()
    ranking = sorted(get_all_scouts(), key=calc_puntos_totales, reverse=True)
    scout = get_scout(str(member.id))
    points = calc_puntos_totales(scout) if scout else 0
    position = next(
        (index for index, row in enumerate(ranking, start=1) if str(row[0]) == str(member.id)),
        None,
    )
    status = get_prio_status(points, cutoff)
    position_text = f"#{position}" if position else "Sin posición"
    prio_text = "Prio" if status["qualifies"] else f"Faltan {status['missing']} pts"
    embed = discord.Embed(
        title=f"{text_emoji('SCOUT')} Mi Ranking",
        color=COLOR_PERFIL,
    )
    top_lines = [
        f"**#{index} {row[1]}** · {calc_puntos_totales(row)} pts"
        for index, row in enumerate(ranking[:3], start=1)
    ]
    embed.add_field(
        name=f"{text_emoji('RANKING')} Top 3",
        value="\n".join(top_lines) if top_lines else "Sin puntos.",
        inline=False,
    )
    embed.add_field(
        name=f"{text_emoji('POINTS')} Tú",
        value=f"**{points} pts** · {position_text} · {prio_text}",
        inline=False,
    )
    return embed


def build_counting_dashboard_embed():
    points_config = {activity: points for activity, points in get_all_config()}
    activity_lines = [
        f"{meta['emoji']} **{meta['label']}** · {points_config.get(key, 0)} pts/u"
        for key, meta in ACTIVIDADES.items()
    ]
    recent_lines = [format_recent_evidence_line(item) for item in get_recent_evidence(3)]
    review_channel = (
        f"<#{EVIDENCE_REVIEW_CHANNEL_ID}>"
        if EVIDENCE_REVIEW_CHANNEL_ID
        else "canal sin configurar"
    )
    embed = discord.Embed(
        title=f"{text_emoji('POINTS')} Conteo",
        description=f"**{get_pending_count()} pendientes** · {review_channel}",
        color=COLOR_PANEL,
    )
    embed.add_field(
        name="Actividades",
        value="\n".join(activity_lines),
        inline=True,
    )
    embed.add_field(
        name=f"{text_emoji('EVIDENCE')} Últimas",
        value="\n".join(recent_lines) if recent_lines else "Sin evidencias.",
        inline=True,
    )
    return embed


def build_admin_dashboard_embed(level: AccessLevel):
    cutoff = get_priority_min_points()
    ranking = get_all_scouts()
    events = get_audit_events(limit=1)
    latest_event = events[0] if events else None
    embed = discord.Embed(
        title=f"{text_emoji('SETTINGS')} Admin",
        description=f"**{len(ranking)} scouts** · **{get_pending_count()} pendientes**",
        color=COLOR_RANKING if level >= AccessLevel.GM_LEADER else COLOR_PANEL,
    )
    embed.add_field(
        name=f"{text_emoji('PRIO')} Prio",
        value=f"**{cutoff} pts**",
        inline=True,
    )
    if level >= AccessLevel.GM_LEADER:
        latest = get_latest_ranking_snapshot()
        next_reset = next_weekly_reset_at()
        embed.add_field(
            name=f"{text_emoji('CALENDAR')} Cierre",
            value=f"**#{latest[0]}** · {short_dashboard_date(latest[3])}" if latest else "Sin cierre.",
            inline=True,
        )
        embed.add_field(
            name=f"{text_emoji('PENDING')} Próximo",
            value=f"<t:{int(next_reset.timestamp())}:R>",
            inline=True,
        )

    embed.add_field(
        name=f"{text_emoji('AUDIT')} Último cambio",
        value=(
            f"{audit_action_label(latest_event.get('action'))} · "
            f"{str(latest_event.get('summary') or 'Sin detalle')[:180]}"
            if latest_event else
            "Sin movimientos."
        ),
        inline=False,
    )
    return embed


def format_recent_evidence_line(item: dict):
    status_emoji = {
        "pending": text_emoji("PENDING"),
        "approved": text_emoji("APPROVED"),
        "rejected": text_emoji("REJECTED"),
    }.get(item.get("status"), text_emoji("EVIDENCE"))
    activity = ACTIVIDADES.get(item.get("activity"), {}).get("label", item.get("activity") or "Evidencia")
    return (
        f"{status_emoji} **{activity}** · {item['points']} pts · "
        f"{item['participants']}p"
    )


def short_dashboard_date(value):
    text = str(value or "").replace("T", " ")
    if len(text) >= 16:
        return f"{text[8:10]}/{text[5:7]} {text[11:16]}"
    return text or "-"


class RankingDashboardView(SafeView):
    def __init__(self):
        super().__init__(timeout=900)
        self.add_item(HubProfileButton())
        self.add_item(HubRankingButton())
        self.add_item(HubRequirementButton())

class CountingDashboardView(SafeView):
    def __init__(self):
        super().__init__(timeout=600)
        for activity_key in ACTIVIDADES:
            self.add_item(CountingActivityButton(activity_key))
        self.add_item(CountingPendingButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if is_admin(interaction):
            return True
        await interaction.response.send_message("Requiere jerarquia Officer / Admin.", ephemeral=True)
        return False


class CountingActivityButton(discord.ui.Button):
    def __init__(self, activity_key: str):
        meta = ACTIVIDADES[activity_key]
        super().__init__(
            label=meta["label"],
            emoji=meta["emoji"],
            style=discord.ButtonStyle.primary if activity_key == "scouteo" else discord.ButtonStyle.secondary,
            row=0,
        )
        self.activity_key = activity_key

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Requiere jerarquia Officer / Admin.", ephemeral=True)
            return
        if self.activity_key == "scouteo":
            await interaction.response.send_modal(ScouteoCountLaunchModal())
            return
        if self.activity_key == "mapeo":
            await analizar_mapeo.callback(interaction, fuente="actual")
            return
        await interaction.response.send_modal(BulkPointsModal(self.activity_key, "actual"))


class CountingPendingButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Ver Pendientes",
            emoji=button_emoji("PENDING"),
            style=discord.ButtonStyle.secondary,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        recent = [format_recent_evidence_line(item) for item in get_recent_evidence(5)]
        channel_text = (
            f"<#{EVIDENCE_REVIEW_CHANNEL_ID}>"
            if EVIDENCE_REVIEW_CHANNEL_ID
            else "Canal sin configurar"
        )
        embed = discord.Embed(
            title=f"{text_emoji('PENDING')} Pendientes",
            description=f"**{get_pending_count()} evidencias** · {channel_text}",
            color=COLOR_WARNING,
        )
        embed.add_field(
            name="Últimas",
            value="\n".join(recent) if recent else "Sin evidencias.",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class AdminDashboardView(SafeView):
    def __init__(self, level: AccessLevel):
        super().__init__(timeout=600)
        self.level = AccessLevel(level)
        self.add_item(HubScoutProfileButton())
        self.add_item(HubBulkPointsButton())
        self.add_item(HubRosterButton())
        self.add_item(HubPublishInfoButton())
        self.add_item(HubAuditButton())
        if self.level >= AccessLevel.GM_LEADER:
            self.add_item(HubPriorityButton())
            self.add_item(HubSettingsButton())
            self.add_item(HubExportButton())
            self.add_item(HubAfkButton())
            self.add_item(HubClosureButton())
            self.add_item(HubSystemButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if is_admin(interaction):
            return True
        await interaction.response.send_message("Requiere jerarquia Officer / Admin.", ephemeral=True)
        return False


class HubProfileButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Mi Perfil",
            emoji=button_emoji("SCOUT"),
            style=discord.ButtonStyle.secondary,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=build_perfil_embed(str(interaction.user.id), interaction.user.display_name),
            ephemeral=True,
        )


class HubRankingButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Ver Ranking",
            emoji=button_emoji("RANKING"),
            style=discord.ButtonStyle.primary,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        from embeds import build_ranking_embed
        from views import RankingPaginationView

        await interaction.response.send_message(
            embed=build_ranking_embed(page=0, per_page=10),
            view=RankingPaginationView(page=0),
            ephemeral=True,
        )


class HubRequirementButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Requisito Prio",
            emoji=button_emoji("PRIO"),
            style=discord.ButtonStyle.secondary,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=build_priority_requirement_embed(),
            ephemeral=True,
        )


class HubEvidenceButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Revisar Evidencias",
            emoji=button_emoji("EVIDENCE"),
            style=discord.ButtonStyle.primary,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        if not can_review_member(interaction.user):
            await interaction.response.send_message("Requiere jerarquia Officer / Admin.", ephemeral=True)
            return
        channel_text = f"<#{EVIDENCE_REVIEW_CHANNEL_ID}>" if EVIDENCE_REVIEW_CHANNEL_ID else "canal no configurado"
        embed = discord.Embed(
            title=f"{text_emoji('EVIDENCE')} Evidencias",
            description=f"**{get_pending_count()} pendientes** · {channel_text}",
            color=COLOR_WARNING,
        )
        await interaction.response.send_message(
            embed=embed,
            view=EvidenceOperationsView(),
            ephemeral=True,
        )


class EvidenceOperationsView(SafeView):
    def __init__(self):
        super().__init__(timeout=600)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if can_review_member(interaction.user):
            return True
        await interaction.response.send_message("Requiere jerarquia Officer / Admin.", ephemeral=True)
        return False

    @discord.ui.button(
        label="Contar Scouteo",
        emoji=button_emoji("SCOUTING"),
        style=discord.ButtonStyle.primary,
    )
    async def scouteo(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ScouteoCountLaunchModal())

    @discord.ui.button(
        label="Analizar Mapeo",
        emoji=button_emoji("MAP"),
        style=discord.ButtonStyle.secondary,
    )
    async def mapping(self, interaction: discord.Interaction, button: discord.ui.Button):
        await analizar_mapeo.callback(interaction, fuente="actual")

    @discord.ui.button(
        label="Ver Pendientes",
        emoji=button_emoji("PENDING"),
        style=discord.ButtonStyle.secondary,
    )
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title=f"{text_emoji('PENDING')} Evidencias Pendientes",
            description=(
                f"Pendientes: **{get_pending_count()}**\n"
                f"Canal: <#{EVIDENCE_REVIEW_CHANNEL_ID}>"
            ),
            color=COLOR_WARNING,
        )
        await interaction.response.edit_message(embed=embed, view=self)


class ScouteoCountLaunchModal(SafeModal):
    message_id = discord.ui.TextInput(
        label="ID del mensaje resumen",
        placeholder="Clic derecho al resumen y Copiar ID",
        max_length=20,
    )
    source = discord.ui.TextInput(
        label="Destino",
        placeholder="auto, actual o ultimo_cierre",
        default="auto",
        max_length=14,
    )

    def __init__(self):
        super().__init__(title="Preparar conteo de scouteo")

    async def on_submit(self, interaction: discord.Interaction):
        source = str(self.source.value or "auto").strip().lower()
        if source not in {"auto", "actual", "ultimo_cierre"}:
            await interaction.response.send_message(
                "Destino invalido. Usa `auto`, `actual` o `ultimo_cierre`.",
                ephemeral=True,
            )
            return
        await conteo.callback(interaction, str(self.message_id.value), source)


class HubScoutProfileButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Editar Scout",
            emoji=button_emoji("SCOUT"),
            style=discord.ButtonStyle.secondary,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"{text_emoji('SCOUT')} Scout",
                color=COLOR_PERFIL,
            ),
            view=ScoutProfilePickerView(),
            ephemeral=True,
        )


class ScoutProfilePickerView(SafeView):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(ScoutProfilePicker())


class ScoutProfilePicker(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(
            placeholder="Selecciona un scout",
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Requiere jerarquia Officer / Admin.", ephemeral=True)
            return
        user = self.values[0]
        await interaction.response.edit_message(
            embed=build_perfil_embed(str(user.id), user.display_name),
            view=AdminProfileView(user),
        )


class HubBulkPointsButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Ajustar Puntos",
            emoji=button_emoji("POINTS"),
            style=discord.ButtonStyle.secondary,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Requiere jerarquia Officer / Admin.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=build_bulk_points_dashboard_embed("actual"),
            view=BulkPointsDashboardView("actual"),
            ephemeral=True,
        )


class HubRosterButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Gestionar Padrón",
            emoji=button_emoji("ROSTER"),
            style=discord.ButtonStyle.secondary,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Requiere jerarquia Officer / Admin.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=build_alias_pattern_dashboard_embed(),
            view=AliasPatternDashboardView(),
            ephemeral=True,
        )


class HubPublishInfoButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Publicar Ranking",
            emoji=button_emoji("PUBLISH"),
            style=discord.ButtonStyle.secondary,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Requiere jerarquia Officer / Admin.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await publish_or_update_info_ranking()
        record_interaction_audit(
            interaction,
            "publicaciones",
            "actualizar_info_ranking",
            target_type="canal",
            target_id=INFO_RANKING_CHANNEL_ID,
            summary="Actualizo la guia publica y el ranking general.",
        )
        await interaction.followup.send(
            f"{text_emoji('APPROVED')} Guia publica y ranking general actualizados.",
            ephemeral=True,
        )


def build_audit_dashboard_embed():
    events = get_audit_events(limit=6)
    embed = discord.Embed(
        title=f"{text_emoji('AUDIT')} Historial",
        description="Últimos 6 · historial completo en MD.",
        color=COLOR_PANEL,
    )
    if not events:
        embed.add_field(
            name=f"{text_emoji('PENDING')} Sin movimientos",
            value="El historial empezara a llenarse con las siguientes acciones.",
            inline=False,
        )
        return embed

    for event in events:
        actor_id = str(event.get("actor_id") or "")
        actor = f"<@{actor_id}>" if actor_id.isdigit() else (event.get("actor_name") or "Sistema")
        timestamp = str(event.get("created_at") or "").replace("T", " ")[:16]
        action = audit_action_label(event.get("action"))
        summary = str(event.get("summary") or "Sin detalle")
        embed.add_field(
            name=f"#{event['id']} · {action}",
            value=f"`{timestamp} UTC` · {actor} · {summary[:140]}",
            inline=False,
        )
    return embed


class HubAuditButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Ver Historial",
            emoji=button_emoji("AUDIT"),
            style=discord.ButtonStyle.secondary,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Requiere jerarquia Officer / Admin.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=build_audit_dashboard_embed(),
            view=AuditDashboardView(),
            ephemeral=True,
        )


class AuditDashboardView(SafeView):
    def __init__(self):
        super().__init__(timeout=600)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if is_admin(interaction):
            return True
        await interaction.response.send_message("Requiere jerarquia Officer / Admin.", ephemeral=True)
        return False

    @discord.ui.button(
        label="Actualizar",
        emoji=button_emoji("REFRESH"),
        style=discord.ButtonStyle.secondary,
    )
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=build_audit_dashboard_embed(), view=self)

    @discord.ui.button(
        label="Exportar MD",
        emoji=button_emoji("EXPORT"),
        style=discord.ButtonStyle.primary,
    )
    async def export_markdown(self, interaction: discord.Interaction, button: discord.ui.Button):
        record_interaction_audit(
            interaction,
            "exportaciones",
            "historial_markdown",
            target_type="auditoria",
            summary="Descargo el historial completo de RankingBot en Markdown.",
        )
        events = get_audit_events(limit=None)
        content = build_audit_markdown(events).encode("utf-8")
        filename = f"historial_rankingbot_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.md"
        await interaction.response.send_message(
            content=f"{text_emoji('EXPORT')} Historial completo: **{len(events)} cambios**.",
            file=discord.File(io.BytesIO(content), filename=filename),
            ephemeral=True,
        )


class HubPriorityButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Gestionar Prio",
            emoji=button_emoji("PRIO"),
            style=discord.ButtonStyle.primary,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Requiere jerarquia GM / Lider.", ephemeral=True)
            return
        role = get_priority_role(interaction)
        if not role:
            await interaction.response.send_message(f"No encontre el rol prio `{PRIORITY_ROLE_ID}`.", ephemeral=True)
            return
        cutoff = get_priority_min_points()
        await interaction.response.send_message(
            embed=build_priority_dashboard_embed(interaction.guild, role, cutoff, "actual"),
            view=PrioDashboardView(cutoff, "actual"),
            ephemeral=True,
        )


class HubSettingsButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Configurar Puntos",
            emoji=button_emoji("POINTS"),
            style=discord.ButtonStyle.secondary,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Requiere jerarquia GM / Lider.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"{text_emoji('POINTS')} Valores",
                description="Puntos por unidad.",
                color=COLOR_PANEL,
            ),
            view=PointsSelectView(),
            ephemeral=True,
        )


class HubExportButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Exportar Ranking",
            emoji=button_emoji("EXPORT"),
            style=discord.ButtonStyle.secondary,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Requiere jerarquia GM / Lider.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"{text_emoji('EXPORT')} Exportar",
                description="Fuente y formato.",
                color=COLOR_PANEL,
            ),
            view=RankingExportView(),
            ephemeral=True,
        )


class RankingExportView(SafeView):
    def __init__(self):
        super().__init__(timeout=300)

    async def send_file(self, interaction: discord.Interaction, source: str, file_format: str):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Requiere jerarquia GM / Lider.", ephemeral=True)
            return
        source_data = get_ranking_export_source(source)
        if source == "ultimo_cierre" and not source_data["snapshot"]:
            await interaction.response.send_message("Aun no existe un cierre semanal.", ephemeral=True)
            return
        filename = build_ranking_export_filename(source_data, file_format)
        file = (
            build_ranking_csv_file(filename, source)
            if file_format == "csv"
            else build_ranking_xlsx_file(filename, source)
        )
        record_interaction_audit(
            interaction,
            "exportaciones",
            "ranking",
            target_type="archivo",
            target_id=filename,
            summary=f"Exporto {source_data['label']} en formato {file_format.upper()}.",
            details={"fuente": source, "formato": file_format},
        )
        await interaction.response.send_message(
            content=f"{text_emoji('EXPORT')} **{source_data['label']}**",
            file=file,
            ephemeral=True,
        )

    @discord.ui.button(
        label="Actual XLSX",
        emoji=button_emoji("EXPORT"),
        style=discord.ButtonStyle.primary,
    )
    async def current_xlsx(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.send_file(interaction, "actual", "xlsx")

    @discord.ui.button(
        label="Actual CSV",
        emoji=button_emoji("EXPORT"),
        style=discord.ButtonStyle.secondary,
    )
    async def current_csv(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.send_file(interaction, "actual", "csv")

    @discord.ui.button(
        label="Cierre XLSX",
        emoji=button_emoji("CALENDAR"),
        style=discord.ButtonStyle.secondary,
    )
    async def closure_xlsx(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.send_file(interaction, "ultimo_cierre", "xlsx")

    @discord.ui.button(
        label="Cierre CSV",
        emoji=button_emoji("CALENDAR"),
        style=discord.ButtonStyle.secondary,
    )
    async def closure_csv(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.send_file(interaction, "ultimo_cierre", "csv")


class HubAfkButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Revisar AFK",
            emoji=button_emoji("AFK"),
            style=discord.ButtonStyle.secondary,
            row=3,
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Requiere jerarquia GM / Lider.", ephemeral=True)
            return
        await afks.callback(interaction)


class HubClosureButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Cerrar Semana",
            emoji=button_emoji("CALENDAR"),
            style=discord.ButtonStyle.danger,
            row=3,
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Requiere jerarquia GM / Lider.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"{text_emoji('CALENDAR')} Cierre",
                description="Guarda el cierre y limpia el ranking. Confirma tras la auditoría.",
                color=COLOR_ERROR,
            ),
            view=ResetView(),
            ephemeral=True,
        )


class HubSystemButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Herramientas GM",
            emoji=button_emoji("SETTINGS"),
            style=discord.ButtonStyle.secondary,
            row=3,
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Requiere jerarquia GM / Lider.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"{text_emoji('SETTINGS')} Sistema",
                description="Paneles · conteos · checkpoint.",
                color=COLOR_PANEL,
            ),
            view=LeaderSystemView(),
            ephemeral=True,
        )


class LeaderSystemView(SafeView):
    def __init__(self):
        super().__init__(timeout=600)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if is_gm_member(interaction.user):
            return True
        await interaction.response.send_message("Requiere jerarquia GM / Lider.", ephemeral=True)
        return False

    @discord.ui.button(
        label="Actualizar Paneles",
        emoji=button_emoji("PANELS"),
        style=discord.ButtonStyle.primary,
    )
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await publish_or_update_dashboard()
        await publish_or_update_info_ranking()
        record_interaction_audit(
            interaction,
            "publicaciones",
            "actualizar_paneles",
            target_type="servidor",
            target_id=interaction.guild_id,
            summary="Actualizo el dashboard publico y la guia del ranking.",
        )
        await interaction.followup.send("Paneles publicos actualizados.", ephemeral=True)

    @discord.ui.button(
        label="Mover Conteo",
        emoji=button_emoji("CALENDAR"),
        style=discord.ButtonStyle.secondary,
    )
    async def move_count(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(MoveCountLaunchModal())

    @discord.ui.button(
        label="Reiniciar Mapeo",
        emoji=button_emoji("MAP"),
        style=discord.ButtonStyle.danger,
    )
    async def reset_mapping(self, interaction: discord.Interaction, button: discord.ui.Button):
        await reset_analisis_mapeo.callback(interaction)


class MoveCountLaunchModal(SafeModal):
    message_id = discord.ui.TextInput(
        label="ID del mensaje/conteo",
        placeholder="ID del resumen ya contado",
        max_length=20,
    )
    closure_id = discord.ui.TextInput(
        label="ID de cierre (opcional)",
        placeholder="Vacio = ultimo cierre",
        required=False,
        max_length=10,
    )

    def __init__(self):
        super().__init__(title="Mover conteo al cierre")

    async def on_submit(self, interaction: discord.Interaction):
        raw_closure = str(self.closure_id.value or "").strip()
        if raw_closure and not raw_closure.isdigit():
            await interaction.response.send_message("El ID de cierre debe ser numerico.", ephemeral=True)
            return
        await mover_conteo_cierre.callback(
            interaction,
            str(self.message_id.value),
            int(raw_closure) if raw_closure else None,
        )


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

    @discord.ui.button(
        label="Sumar",
        emoji=button_emoji("POINTS"),
        style=discord.ButtonStyle.success,
    )
    async def add_points(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=build_admin_profile_action_embed(self.user_id, self.display_name, "sumar"),
            view=AdminProfileActivityView(self.user_id, self.display_name, "sumar"),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Restar",
        emoji=button_emoji("REJECTED"),
        style=discord.ButtonStyle.danger,
    )
    async def subtract_points(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=build_admin_profile_action_embed(self.user_id, self.display_name, "restar"),
            view=AdminProfileActivityView(self.user_id, self.display_name, "restar"),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Actualizar",
        emoji=button_emoji("REFRESH"),
        style=discord.ButtonStyle.secondary,
    )
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
        super().__init__(title=f"{verb} puntos · {meta['label']}")

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

        record_interaction_audit(
            interaction,
            "puntos",
            self.action,
            target_type="scout",
            target_id=self.user_id,
            summary=(
                f"{self.action.title()} {units} unidad(es) de {ACTIVIDADES[self.actividad_key]['label']} "
                f"({changed_points} pts) a {self.display_name}."
            ),
            details={
                "actividad": self.actividad_key,
                "unidades": units,
                "puntos_solicitados": requested_points,
                "puntos_aplicados": changed_points,
                "motivo": str(self.motivo.value or ""),
                "destino": "ranking_actual",
            },
        )
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
        title=f"{text_emoji('POINTS')} Elegir actividad para {verb}",
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
        title=f"{text_emoji('APPROVED')} Puntos ajustados",
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
    minimo: int | None = None,
    fuente: str = "actual",
):
    if not is_gm_member(interaction.user):
        await interaction.response.send_message("Esta accion requiere jerarquia GM / Lider.", ephemeral=True)
        return

    requested_minimum = minimo
    minimo = get_priority_min_points() if minimo is None else minimo
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
    if requested_minimum is not None:
        previous = get_priority_min_points()
        set_priority_min_points(minimo)
        record_interaction_audit(
            interaction,
            "prio",
            "cambiar_corte",
            target_type="configuracion_prio",
            summary=f"Cambio el corte de prio de {previous} a {minimo} puntos.",
            details={"antes": previous, "despues": minimo, "fuente": source},
        )
        await publish_or_update_dashboard()
        await publish_or_update_info_ranking()


@admin_group.command(name="info_ranking", description="Publica la guía y ranking general")
async def info_ranking(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return

    await publish_or_update_info_ranking()
    record_interaction_audit(
        interaction,
        "publicaciones",
        "actualizar_info_ranking",
        target_type="canal",
        target_id=INFO_RANKING_CHANNEL_ID,
        summary="Actualizo la guia publica y el ranking general.",
    )
    await interaction.response.send_message("Info ranking actualizada.", ephemeral=True)


async def publish_or_update_info_ranking():
    channel = bot.get_channel(INFO_RANKING_CHANNEL_ID)
    if not channel:
        channel = await bot.fetch_channel(INFO_RANKING_CHANNEL_ID)

    embed = build_info_ranking_embed()
    info_msg = None
    async for msg in channel.history(limit=20):
        title = msg.embeds[0].title if msg.embeds else ""
        if msg.author.id == bot.user.id and (
            "Guía de Evidencias" in (title or "") or
            "Ranking de Evidencias" in (title or "")
        ):
            info_msg = msg
            break

    if info_msg:
        await info_msg.edit(embed=embed, view=InfoRankingView())
    else:
        await channel.send(embed=embed, view=InfoRankingView())


async def refresh_public_messages_from_message(message: discord.Message):
    try:
        await publish_or_update_dashboard()
        await publish_or_update_info_ranking()
    except discord.HTTPException as error:
        print(f"[PUBLIC PANELS] No se pudieron actualizar tras evidencia {message.id}: {error}")


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

    record_interaction_audit(
        interaction,
        "puntos",
        "sumar" if cantidad > 0 else "restar",
        target_type="scout",
        target_id=usuario.id,
        summary=(
            f"{'Sumo' if cantidad > 0 else 'Resto'} {abs(applied_quantity)} unidad(es) de "
            f"{ACTIVIDADES[actividad_key]['label']} ({pts} pts) a {usuario.display_name}."
        ),
        details={
            "actividad": actividad_key,
            "unidades": applied_quantity,
            "puntos": pts,
            "destino": source_label,
        },
    )
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
        title=f"{text_emoji('POINTS')} Ajuste Masivo de Puntos",
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
        super().__init__(title=f"Ajuste masivo · {meta['label']}")

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

        if applied:
            record_interaction_audit(
                interaction,
                "puntos",
                f"{action}_masivo",
                target_type="grupo_scouts",
                target_id=len(applied),
                summary=(
                    f"{action.title()} {units} unidad(es) de {ACTIVIDADES[self.actividad_key]['label']} "
                    f"a {len(applied)} scout(s) en {self.source}."
                ),
                details={
                    "actividad": self.actividad_key,
                    "unidades_por_scout": units,
                    "scouts": ", ".join(f"{username} ({user_id})" for user_id, username, *_ in applied),
                    "destino": self.source,
                    "motivo": str(self.motivo.value or ""),
                    "no_resueltos": len(unresolved),
                },
            )
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
        title=(
            f"{text_emoji('APPROVED')} Carga masiva aplicada"
            if applied else
            f"{text_emoji('PENDING')} Carga masiva sin aplicar"
        ),
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
        title=f"{text_emoji('ROSTER')} Padrón de Scouts",
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

    @discord.ui.button(
        label="Actualizar",
        emoji=button_emoji("REFRESH"),
        style=discord.ButtonStyle.secondary,
    )
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        await interaction.response.edit_message(embed=build_alias_pattern_dashboard_embed(), view=self)

    @discord.ui.button(
        label="Exportar",
        emoji=button_emoji("EXPORT"),
        style=discord.ButtonStyle.primary,
    )
    async def export_xlsx(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        await interaction.response.send_message(
            content="Edita la columna `aliases` y vuelve a importarlo desde este panel.",
            file=build_alias_pattern_xlsx_file(),
            ephemeral=True,
        )
        record_interaction_audit(
            interaction,
            "exportaciones",
            "padron_aliases",
            target_type="archivo",
            target_id="padron_aliases.xlsx",
            summary="Exporto el padron de aliases en XLSX.",
        )

    @discord.ui.button(
        label="Importar",
        emoji=button_emoji("IMPORT"),
        style=discord.ButtonStyle.success,
    )
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

        record_interaction_audit(
            interaction,
            "padron",
            "importar_aliases",
            target_type="archivo",
            target_id=attachment.filename,
            summary=(
                f"Importo aliases: {len(result['saved'])} nuevos, "
                f"{len(result['replaced'])} reasignados."
            ),
            details={
                "guardados": len(result["saved"]),
                "reasignados": len(result["replaced"]),
                "sin_cambio": len(result["unchanged"]),
                "no_resueltos": len(result["unresolved"]),
            },
        )
        await interaction.followup.send(embed=build_alias_import_result_embed(result), ephemeral=True)

    @discord.ui.button(
        label="Agregar",
        emoji=button_emoji("APPROVED"),
        style=discord.ButtonStyle.secondary,
    )
    async def add_manual(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        await interaction.response.send_modal(AliasManualAddModal())

    @discord.ui.button(
        label="Quitar",
        emoji=button_emoji("REJECTED"),
        style=discord.ButtonStyle.danger,
    )
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

        if result["saved"] or result["replaced"]:
            record_interaction_audit(
                interaction,
                "padron",
                "agregar_aliases",
                target_type="scout",
                target_id=target["user_id"] if target else None,
                summary=(
                    f"Registro {len(result['saved']) + len(result['replaced'])} alias(es) "
                    f"para {target['username'] if target else 'scout no resuelto'}."
                ),
                details={
                    "guardados": len(result["saved"]),
                    "reasignados": len(result["replaced"]),
                    "aliases": ", ".join(item[2] for item in result["saved"] + result["replaced"]),
                },
            )
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
            title=(
                f"{text_emoji('APPROVED')} Aliases quitados"
                if removed else
                f"{text_emoji('PENDING')} No encontre aliases"
            ),
            color=COLOR_SUCCESS if removed else COLOR_WARNING,
        )
        if removed:
            embed.add_field(name="Quitados", value=", ".join(f"`+{alias}`" for alias in removed[:30])[:1000], inline=False)
        if missing:
            embed.add_field(name="No encontrados", value=", ".join(f"`+{alias}`" for alias in missing[:30])[:1000], inline=False)
        if removed:
            record_interaction_audit(
                interaction,
                "padron",
                "quitar_aliases",
                target_type="aliases",
                target_id=len(removed),
                summary=f"Quito {len(removed)} alias(es) del padron.",
                details={"aliases": ", ".join(removed)},
            )
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
        title=f"{text_emoji('IMPORT')} Importación de Padrón",
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
    if not is_gm_member(interaction.user):
        await interaction.response.send_message("Esta accion requiere jerarquia GM / Lider.", ephemeral=True)
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
    record_interaction_audit(
        interaction,
        "exportaciones",
        "ranking",
        target_type="archivo",
        target_id=filename,
        summary=f"Exporto {source_data['label']} en formato {file_format.upper()}.",
        details={"fuente": source, "formato": file_format},
    )
    await interaction.response.send_message(
        content=f"{text_emoji('EXPORT')} Export: **{source_data['label']}**",
        file=file,
        ephemeral=True,
    )


def build_ranking_csv_file(filename: str, source: str = "actual"):
    source_data = get_ranking_export_source(source)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["user_id", "username", *ACTIVIDADES.keys(), "total_puntos", "estado_prio", "detalle_prio"])

    for row in get_ranking_export_rows(source_data):
        writer.writerow(row)

    output.seek(0)
    return discord.File(fp=io.BytesIO(output.getvalue().encode("utf-8")), filename=filename)


def build_ranking_xlsx_file(filename: str, source: str = "actual"):
    source_data = get_ranking_export_source(source)
    rows = [
        ["Fuente", source_data["label"]],
        [],
        ["user_id", "username", *ACTIVIDADES.keys(), "total_puntos", "estado_prio", "detalle_prio"],
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
            pts = int(row[7] or 0)
            base = list(row[:8])
        else:
            pts = calc_puntos_totales(row)
            base = [*row, pts]
        _, estado, detalle = row_points_level(base, get_priority_min_points())
        exported.append([*base, estado, detalle])
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
    if not is_gm_member(interaction.user):
        await interaction.response.send_message("Esta accion requiere jerarquia GM / Lider.", ephemeral=True)
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
        title=f"{text_emoji('AFK')} Revisión de Inactividad",
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
            label="Criterio",
            emoji=button_emoji("POINTS"),
            style=discord.ButtonStyle.primary,
            row=0,
        )
        self.review_view = review_view

    async def callback(self, interaction: discord.Interaction):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Requiere jerarquia GM / Lider.", ephemeral=True)
            return
        await interaction.response.send_modal(InactivePointsModal(self.review_view))


class KickInactiveButton(discord.ui.Button):
    def __init__(self, review_view: InactiveReviewView):
        super().__init__(
            label="Kick",
            emoji=button_emoji("AFK"),
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
                emoji=button_emoji("AFK"),
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
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Requiere jerarquia GM / Lider.", ephemeral=True)
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
        record_interaction_audit(
            interaction,
            "afk",
            "descartar_candidatos",
            target_type="revision_afk",
            target_id=interaction.message.id if interaction.message else None,
            summary=f"Descarto {len(selected)} candidato(s) de la revision AFK.",
            details={
                "candidatos": ", ".join(f"{item['username']} ({item['user_id']})" for item in selected),
                "corte": self.review_view.max_points,
            },
        )
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
        record_interaction_audit(
            interaction,
            "afk",
            "kickear_candidatos",
            target_type="servidor",
            target_id=interaction.guild.id,
            summary=(
                f"Kickeo {len(result['kicked'])} miembro(s) por inactividad; "
                f"{len(result['errors'])} error(es)."
            ),
            details={
                "corte": self.review_view.max_points,
                "kickeados": ", ".join(f"{member.display_name} ({member.id})" for member in result["kicked"]),
                "no_encontrados": len(result["missing"]),
                "omitidos": len(result["skipped"]),
                "errores": len(result["errors"]),
            },
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
        super().__init__(title="Cambiar criterio AFK")
        self.review_view = review_view
        self.points.default = str(review_view.max_points)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Requiere jerarquia GM / Lider.", ephemeral=True)
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

        previous = self.review_view.max_points
        await interaction.response.defer(ephemeral=True, thinking=True)
        members = await get_non_bot_guild_members(interaction.guild)
        candidates, summary = build_inactive_candidates(members, max_points)
        self.review_view.candidates = candidates
        self.review_view.summary = summary
        self.review_view.max_points = max_points
        record_interaction_audit(
            interaction,
            "afk",
            "cambiar_criterio",
            target_type="revision_afk",
            target_id=interaction.message.id if interaction.message else None,
            summary=f"Cambio el maximo semanal AFK de {previous} a {max_points} puntos.",
            details={
                "antes": previous,
                "despues": max_points,
                "candidatos": len(candidates),
            },
        )
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
        title=f"{text_emoji('AFK')} Resultado de Kicks AFK",
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


def row_points_level(row, minimum: int | None = None):
    if len(row) >= 10:
        points = int(row[7] or 0)
    else:
        points = calc_puntos_totales(row)
    cutoff = get_priority_min_points() if minimum is None else max(0, int(minimum))
    qualifies = points >= cutoff
    status = "Califica" if qualifies else "No califica"
    detail = f"Cumple {cutoff}+ pts" if qualifies else f"Le faltan {cutoff - points} pts"
    return points, status, detail


def build_priority_candidates(minimo: int, ranking_rows=None):
    candidates = []
    for row in (ranking_rows if ranking_rows is not None else get_all_scouts()):
        points, estado, detalle = row_points_level(row, minimo)
        if points < minimo:
            continue
        candidates.append({
            "user_id": str(row[0]),
            "username": row[1],
            "points": points,
            "estado": estado,
            "detalle": detalle,
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
        f"`#{index}` {format_priority_user(item)} · **{item['points']} pts** · {item['motivo']}"
        for index, item in enumerate(rows[:15], start=1)
    ]
    embed = discord.Embed(
        title=f"{text_emoji('PRIO')} Revisión Semanal de Prio",
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
        name=f"{text_emoji('AUDIT')} Vista previa",
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
        title=f"{text_emoji('PRIO')} Gestión de Prio",
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
    writer.writerow(["user_id", "username", "total_puntos", "estado_prio", "detalle", "motivo"])
    protected_members = get_priority_protected_members(guild) if guild else []
    for item in build_priority_export_rows(minimo, protected_members, source_data["rows"]):
        writer.writerow([
            item["user_id"],
            item["username"],
            item["points"],
            item["estado"],
            item["detalle"],
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
            points, estado, detalle = row_points_level(scout, minimo)
        else:
            points = 0
            _, estado, detalle = row_points_level(
                (user_id, member.display_name, 0, 0, 0, 0, 0),
                minimo,
            )
        rows.append({
            "user_id": user_id,
            "username": member.display_name,
            "points": points,
            "estado": estado,
            "detalle": detalle,
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
        title=f"{text_emoji('PRIO')} Roles de Prio Actualizados",
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


def priority_post_winners(result: dict):
    publishable_ids = {
        str(member.id)
        for member in [*result.get("assigned", []), *result.get("already", [])]
    }
    return [
        item for item in result.get("candidates", [])
        if is_discord_user_id(item.get("user_id")) and str(item["user_id"]) in publishable_ids
    ]


def build_priority_mention_chunks(mentions: list[str], header: str, max_length: int = 1850):
    if not mentions:
        return [header]

    chunks = []
    current = header
    for mention in mentions:
        candidate = f"{current}\n{mention}" if current else mention
        if len(candidate) > max_length:
            chunks.append(current)
            current = mention
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def build_priority_embed_line_chunks(lines: list[str], max_length: int = 950):
    chunks = []
    current = []
    current_length = 0
    for line in lines:
        extra = len(line) + (1 if current else 0)
        if current and current_length + extra > max_length:
            chunks.append(current)
            current = [line]
            current_length = len(line)
        else:
            current.append(line)
            current_length += extra
    if current:
        chunks.append(current)
    return chunks


def priority_score_page_count(winners: list[dict]):
    if not winners:
        return 1
    return max(1, (len(winners) + PRIO_SCORE_PAGE_SIZE - 1) // PRIO_SCORE_PAGE_SIZE)


def build_priority_public_post(minimo: int, role: discord.Role, result: dict, page: int = 0):
    winners = priority_post_winners(result)
    mentions = [f"<@{item['user_id']}>" for item in winners]
    source_label = result.get("source_label", "Ranking actual")
    header = (
        "Se cerro la prio semanal. Estos jugadores ganaron prioridad por ranking:"
        if winners else
        "Se cerro la prio semanal, pero nadie alcanzo el corte por ranking."
    )
    content_chunks = build_priority_mention_chunks(mentions, header)

    total_pages = priority_score_page_count(winners)
    page = max(0, min(int(page or 0), total_pages - 1))
    start_index = page * PRIO_SCORE_PAGE_SIZE
    page_winners = winners[start_index:start_index + PRIO_SCORE_PAGE_SIZE]
    winner_lines = [
        f"`#{start_index + index}` <@{item['user_id']}> · **{item['points']} pts**"
        for index, item in enumerate(page_winners, start=1)
    ]
    base_description = (
        f"Corte aplicado: **{minimo} puntos o mas**\n"
        f"Fuente: **{source_label}**\n"
        f"Rol: {role.mention}\n"
        f"Publicados por puntaje: **{len(winners)}**\n"
        f"Pagina: **{page + 1}/{total_pages}**"
    )
    score_text = "\n".join(winner_lines) if winner_lines else "Nadie alcanzo el corte."
    if winners:
        page_start = start_index + 1
        page_end = start_index + len(page_winners)
        score_title = "Puntaje" if total_pages == 1 else f"Puntaje {page_start}-{page_end}"
    else:
        score_title = "Puntaje"
    full_description = f"{base_description}\n\n**{score_title}**\n{score_text}"
    embed = discord.Embed(
        title=f"{text_emoji('PRIO')} Ganadores de Prio",
        description=full_description if len(full_description) <= 4096 else base_description,
        color=COLOR_RANKING,
    )
    line_chunks = []
    if len(full_description) > 4096:
        line_chunks = build_priority_embed_line_chunks(winner_lines)
        if line_chunks:
            for index, chunk in enumerate(line_chunks[:20], start=1):
                start = sum(len(previous) for previous in line_chunks[:index - 1]) + 1
                end = start + len(chunk) - 1
                name = "Puntaje" if len(line_chunks) == 1 else f"Puntaje {start}-{end}"
                embed.add_field(name=name, value="\n".join(chunk), inline=False)
        else:
            embed.add_field(name="Puntaje", value="Nadie alcanzo el corte.", inline=False)
    if len(line_chunks) > 20:
        embed.add_field(
            name="Lista continua",
            value="La lista completa fue pingueada en el texto del mensaje.",
            inline=False,
        )
    embed.set_footer(text="Usa los botones para cambiar de pagina.")
    return content_chunks, embed, winners


class PrioDashboardView(SafeView):
    def __init__(self, minimo: int = DEFAULT_PRIORITY_MIN_POINTS, source: str = "actual"):
        super().__init__(timeout=600)
        self.minimo = max(0, int(DEFAULT_PRIORITY_MIN_POINTS if minimo is None else minimo))
        self.source = normalize_priority_source(source)

    @discord.ui.button(
        label="Actualizar",
        emoji=button_emoji("REFRESH"),
        style=discord.ButtonStyle.secondary,
    )
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Requiere jerarquia GM / Lider.", ephemeral=True)
            return
        role = get_priority_role(interaction)
        if not role:
            await interaction.response.send_message(f"No encontre el rol prio `{PRIORITY_ROLE_ID}`.", ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=build_priority_dashboard_embed(interaction.guild, role, self.minimo, self.source),
            view=self,
        )

    @discord.ui.button(
        label="Corte",
        emoji=button_emoji("PRIO"),
        style=discord.ButtonStyle.primary,
    )
    async def change_cutoff(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Requiere jerarquia GM / Lider.", ephemeral=True)
            return
        await interaction.response.send_modal(PrioCutoffModal(self.minimo, self.source))

    @discord.ui.button(
        label="CSV",
        emoji=button_emoji("EXPORT"),
        style=discord.ButtonStyle.secondary,
    )
    async def export_csv(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Requiere jerarquia GM / Lider.", ephemeral=True)
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
        record_interaction_audit(
            interaction,
            "exportaciones",
            "prio_csv",
            target_type="archivo",
            target_id="prio_semanal.csv",
            summary=f"Exporto la decision de prio con corte {self.minimo}.",
            details={"fuente": self.source, "corte": self.minimo},
        )
        await interaction.followup.send(embed=embed, file=file, ephemeral=True)

    @discord.ui.button(
        label="Requisito",
        emoji=button_emoji("POINTS"),
        style=discord.ButtonStyle.secondary,
    )
    async def caps(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=build_priority_requirement_embed(), ephemeral=True)

    @discord.ui.button(
        label="Aplicar",
        emoji=button_emoji("APPROVED"),
        style=discord.ButtonStyle.danger,
    )
    async def apply_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Requiere jerarquia GM / Lider.", ephemeral=True)
            return
        await interaction.response.send_modal(PrioApplyConfirmModal(self.minimo, self.source))


class PriorityPostActionView(SafeView):
    def __init__(self, minimo: int, source: str, role_id: int, result: dict):
        super().__init__(timeout=900)
        self.minimo = minimo
        self.source = normalize_priority_source(source)
        self.role_id = int(role_id)
        self.result = result

    @discord.ui.button(
        label="Publicar",
        emoji=button_emoji("PRIO"),
        style=discord.ButtonStyle.success,
    )
    async def preview_post(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Requiere jerarquia GM / Lider.", ephemeral=True)
            return
        if not interaction.guild:
            await interaction.response.send_message("Este boton solo funciona dentro del servidor.", ephemeral=True)
            return

        role = interaction.guild.get_role(self.role_id)
        if not role:
            await interaction.response.send_message(f"No encontre el rol prio `{self.role_id}`.", ephemeral=True)
            return

        content_chunks, embed, winners = build_priority_public_post(self.minimo, role, self.result, page=0)
        preview = (
            f"Preview del post en <#{PRIO_POST_CHANNEL_ID}>.\n\n"
            f"{content_chunks[0]}"
        )
        if len(content_chunks) > 1:
            preview += f"\n\nAdemas se enviaran `{len(content_chunks) - 1}` mensaje(s) extra para pinguear a todos."
        if winners:
            preview += "\n\nLos pings estan desactivados en este preview; solo pinguearan al publicar."
        await interaction.response.send_message(
            content=preview[:2000],
            embed=embed,
            view=PriorityPostConfirmView(self.minimo, self.source, self.role_id, self.result, page=0),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class PriorityPublishedScoreView(SafeView):
    def __init__(self, minimo: int, source: str, role_id: int, result: dict, page: int = 0):
        super().__init__(timeout=86400)
        self.minimo = minimo
        self.source = normalize_priority_source(source)
        self.role_id = int(role_id)
        self.result = result
        self.page = int(page or 0)
        self.refresh_buttons()

    def total_pages(self):
        return priority_score_page_count(priority_post_winners(self.result))

    def refresh_buttons(self):
        total_pages = self.total_pages()
        self.page = max(0, min(self.page, total_pages - 1))
        for item in self.children:
            if getattr(item, "custom_id", None) == "prio_post_prev":
                item.disabled = self.page <= 0
            elif getattr(item, "custom_id", None) == "prio_post_next":
                item.disabled = self.page >= total_pages - 1

    async def edit_page(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(self.role_id) if interaction.guild else None
        if not role:
            await interaction.response.send_message(f"No encontre el rol prio `{self.role_id}`.", ephemeral=True)
            return
        _, embed, _ = build_priority_public_post(self.minimo, role, self.result, page=self.page)
        self.refresh_buttons()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Anterior", style=discord.ButtonStyle.secondary, custom_id="prio_post_prev")
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        await self.edit_page(interaction)

    @discord.ui.button(label="Siguiente", style=discord.ButtonStyle.secondary, custom_id="prio_post_next")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        await self.edit_page(interaction)


class PriorityPostConfirmView(SafeView):
    def __init__(self, minimo: int, source: str, role_id: int, result: dict, page: int = 0):
        super().__init__(timeout=300)
        self.minimo = minimo
        self.source = normalize_priority_source(source)
        self.role_id = int(role_id)
        self.result = result
        self.page = int(page or 0)
        self.published = False
        self.refresh_buttons()

    def total_pages(self):
        return priority_score_page_count(priority_post_winners(self.result))

    def refresh_buttons(self):
        total_pages = self.total_pages()
        self.page = max(0, min(self.page, total_pages - 1))
        for item in self.children:
            if getattr(item, "custom_id", None) == "prio_preview_prev":
                item.disabled = self.page <= 0 or self.published
            elif getattr(item, "custom_id", None) == "prio_preview_next":
                item.disabled = self.page >= total_pages - 1 or self.published
            elif getattr(item, "custom_id", None) == "prio_preview_publish":
                item.disabled = self.published

    def preview_content(self, content_chunks: list[str], winners: list[dict]):
        preview = (
            f"Preview del post en <#{PRIO_POST_CHANNEL_ID}>.\n\n"
            f"{content_chunks[0]}"
        )
        if len(content_chunks) > 1:
            preview += f"\n\nAdemas se enviaran `{len(content_chunks) - 1}` mensaje(s) extra para pinguear a todos."
        if winners:
            preview += "\n\nLos pings estan desactivados en este preview; solo pinguearan al publicar."
        return preview[:2000]

    async def edit_page(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(self.role_id) if interaction.guild else None
        if not role:
            await interaction.response.send_message(f"No encontre el rol prio `{self.role_id}`.", ephemeral=True)
            return
        content_chunks, embed, winners = build_priority_public_post(self.minimo, role, self.result, page=self.page)
        self.refresh_buttons()
        await interaction.response.edit_message(
            content=self.preview_content(content_chunks, winners),
            embed=embed,
            view=self,
        )

    @discord.ui.button(label="Anterior", style=discord.ButtonStyle.secondary, custom_id="prio_preview_prev")
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Requiere jerarquia GM / Lider.", ephemeral=True)
            return
        self.page -= 1
        await self.edit_page(interaction)

    @discord.ui.button(
        label="Publicar",
        emoji=button_emoji("PRIO"),
        style=discord.ButtonStyle.success,
        custom_id="prio_preview_publish",
    )
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Requiere jerarquia GM / Lider.", ephemeral=True)
            return
        if self.published:
            await interaction.response.send_message("Este post ya fue publicado desde este preview.", ephemeral=True)
            return
        if not interaction.guild:
            await interaction.response.send_message("Este boton solo funciona dentro del servidor.", ephemeral=True)
            return

        role = interaction.guild.get_role(self.role_id)
        if not role:
            await interaction.response.send_message(f"No encontre el rol prio `{self.role_id}`.", ephemeral=True)
            return

        channel = interaction.client.get_channel(PRIO_POST_CHANNEL_ID)
        if not channel:
            try:
                channel = await interaction.client.fetch_channel(PRIO_POST_CHANNEL_ID)
            except discord.HTTPException:
                await interaction.response.send_message("No pude abrir el canal de posteo de prio.", ephemeral=True)
                return

        content_chunks, embed, winners = build_priority_public_post(self.minimo, role, self.result, page=self.page)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            first_message = await channel.send(
                content=content_chunks[0],
                embed=embed,
                view=PriorityPublishedScoreView(self.minimo, self.source, self.role_id, self.result, self.page),
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
            for chunk in content_chunks[1:]:
                await channel.send(
                    content=chunk,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
        except discord.HTTPException as err:
            await interaction.followup.send(f"No pude publicar el post de prio: `{err}`", ephemeral=True)
            return

        record_interaction_audit(
            interaction,
            "prio",
            "publicar_ganadores",
            target_type="mensaje",
            target_id=first_message.id,
            summary=f"Publico {len(winners)} ganador(es) de prio con corte {self.minimo}.",
            details={
                "canal_id": channel.id,
                "fuente": self.source,
                "corte": self.minimo,
                "ganadores": len(winners),
            },
        )
        self.published = True
        self.refresh_buttons()
        await interaction.edit_original_response(view=self)
        await interaction.followup.send(
            f"Post publicado en {channel.mention}: {first_message.jump_url}\n"
            f"Menciones publicadas: `{len(winners)}`.",
            ephemeral=True,
        )

    @discord.ui.button(label="Siguiente", style=discord.ButtonStyle.secondary, custom_id="prio_preview_next")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Requiere jerarquia GM / Lider.", ephemeral=True)
            return
        self.page += 1
        await self.edit_page(interaction)


class PrioCutoffModal(SafeModal):
    value = discord.ui.TextInput(label="Corte de puntos", placeholder="Ej: 50", max_length=4)

    def __init__(self, current_minimo: int, source: str = "actual"):
        super().__init__(title="Cambiar corte de prio")
        self.source = normalize_priority_source(source)
        self.value.default = str(current_minimo)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Requiere jerarquia GM / Lider.", ephemeral=True)
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

        previous = get_priority_min_points()
        minimo = set_priority_min_points(minimo)
        record_interaction_audit(
            interaction,
            "prio",
            "cambiar_corte",
            target_type="configuracion_prio",
            summary=f"Cambio el corte de prio de {previous} a {minimo} puntos.",
            details={"antes": previous, "despues": minimo, "fuente": self.source},
        )
        await interaction.response.edit_message(
            embed=build_priority_dashboard_embed(interaction.guild, role, minimo, self.source),
            view=PrioDashboardView(minimo, self.source),
        )
        await publish_or_update_dashboard()
        await publish_or_update_info_ranking()


class PrioApplyConfirmModal(SafeModal):
    confirmation = discord.ui.TextInput(label="Confirmacion", placeholder="Escribe APLICAR", max_length=10)

    def __init__(self, minimo: int, source: str = "actual"):
        super().__init__(title=f"Aplicar prio · {minimo}+ pts")
        self.minimo = minimo
        self.source = normalize_priority_source(source)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Requiere jerarquia GM / Lider.", ephemeral=True)
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
        record_interaction_audit(
            interaction,
            "prio",
            "aplicar_roles",
            target_type="rol",
            target_id=role.id,
            summary=(
                f"Aplico prio con corte {self.minimo}: "
                f"{len(result['assigned'])} agregados, {len(result['removed'])} quitados."
            ),
            details={
                "corte": self.minimo,
                "fuente": self.source,
                "agregados": len(result["assigned"]),
                "quitados": len(result["removed"]),
                "ya_tenian": len(result.get("already") or []),
                "protegidos": len(result.get("kept_protected") or []),
                "faltantes": len(result.get("missing") or []),
                "errores": len(result.get("errors") or []),
            },
        )
        embed = build_priority_apply_embed(self.minimo, role, result)
        file = build_priority_csv_file(self.minimo, interaction.guild, self.source)
        await interaction.followup.send(
            embed=embed,
            file=file,
            view=PriorityPostActionView(self.minimo, self.source, role.id, result),
            ephemeral=True,
        )


def get_priority_role(interaction: discord.Interaction):
    if not interaction.guild:
        return None
    return interaction.guild.get_role(PRIORITY_ROLE_ID)


def archive_current_weekly_ranking(reason: str):
    period_end = datetime.now(timezone.utc)
    period_start = current_weekly_ranking_start()
    if period_end - period_start < timedelta(days=1):
        period_start = period_start - timedelta(days=7)
    return create_ranking_snapshot(
        period_start,
        period_end,
        reason,
    )


@admin_group.command(name="reset_ranking", description="Resetea todos los puntos del ranking")
async def reset_ranking(interaction: discord.Interaction, confirmacion: str):
    if not is_gm_member(interaction.user):
        await interaction.response.send_message("Esta accion requiere jerarquia GM / Lider.", ephemeral=True)
        return

    if confirmacion != "RESET":
        await interaction.response.send_message("Escribe `RESET` en confirmacion para resetear el ranking.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    snapshot_id = archive_current_weekly_ranking("manual_reset")
    reset_all()
    record_interaction_audit(
        interaction,
        "cierres",
        "reset_manual",
        target_type="cierre",
        target_id=snapshot_id,
        summary=(
            f"Archivo el ranking en el cierre #{snapshot_id} y limpio el periodo actual."
            if snapshot_id else
            "Limpio el periodo actual; no habia puntos para archivar."
        ),
        details={"snapshot_id": snapshot_id},
    )
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
            record_audit_event(
                "cierres",
                "reset_automatico",
                actor_name="Sistema",
                target_type="cierre",
                target_id=str(snapshot_id) if snapshot_id else None,
                summary=(
                    f"Archivo el ranking en el cierre #{snapshot_id} y limpio el periodo automaticamente."
                    if snapshot_id else
                    "Limpio el periodo automaticamente; no habia puntos para archivar."
                ),
                details={"snapshot_id": snapshot_id},
            )
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


# Los flujos internos conservan sus callbacks y validaciones sin registrarse
# como slash commands pequeños. /ranking, /conteo y /admin los agrupan por
# propósito y jerarquía.


if __name__ == "__main__":
    bot.run(TOKEN)
