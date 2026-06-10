import os
import csv
import io
import asyncio
import traceback
import re
import unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from database import (
    add_activity,
    calc_puntos_totales,
    create_evidence_review,
    find_scout_by_name,
    get_all_scouts,
    get_bot_state,
    get_nivel,
    get_pending_evidence_message_ids,
    init_db,
    reset_all,
    set_bot_state,
    set_evidence_review_message,
    subtract_activity,
)
from config import ACTIVIDADES, APPLICATION_ID, COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING, \
    DASHBOARD_CHANNEL_ID, GUILD_ID, INFO_RANKING_CHANNEL_ID, \
    WEEKLY_EXPORT_CHANNEL_ID, \
    EVIDENCE_CATEGORY, EVIDENCE_CATEGORY_ID, EVIDENCE_CATEGORY_IDS, EVIDENCE_CHANNEL_IDS, \
    EVIDENCE_CHANNELS, EVIDENCE_REVIEW_CHANNEL_ID, IMAGE_EXTENSIONS, LOG_CHANNEL_ID, \
    AUTO_RESET_ENABLED, AUTO_RESET_HOUR_UTC, AUTO_RESET_MINUTE_UTC, AUTO_RESET_WEEKDAY_UTC
from views import DashboardView, EvidenceAuthorConfirmView, EvidenceReviewView, InfoRankingView
from embeds import build_dashboard_embed, build_info_ranking_embed, build_perfil_embed
from permissions import is_admin
from ocr import improve_confidence_for_channel, is_ineligible_ocr, read_message_ocr, suggest_activity_from_ocr

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
    if message.author.bot or not message.guild:
        return

    if should_reply_to_aura_taunt(message.content):
        try:
            await message.reply(AURA_TAUNT_RESPONSE, mention_author=True)
        except discord.HTTPException:
            pass

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
    if ocr_text:
        embed.add_field(name="OCR", value=ocr_text[:1000], inline=False)
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
            value=format_participant_suggestions(suggested_participants)[:1000],
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

    if suggested_participants:
        await message.reply(
            embed=build_participant_confirmation_embed(suggested_participants, unresolved_names),
            view=EvidenceAuthorConfirmView(
                str(message.id),
                str(message.author.id),
                suggested_participants,
                review_msg,
            ),
            mention_author=True,
        )

    done_embed = discord.Embed(
        title="Evidencia enviada a revision",
        description=f"Revision: {review_msg.jump_url}",
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
    unresolved = []
    suggestions = []

    for member in message.mentions:
        if not member.bot:
            participants[str(member.id)] = member.display_name

    for name in extract_plus_names(message.content):
        member = resolve_member_by_name(message.guild, name) or await query_exact_member_by_name(message.guild, name)
        if member and not member.bot:
            participants[str(member.id)] = member.display_name
            continue

        found = find_scout_by_name(name)
        if found:
            participants[str(found[0])] = found[1]
            continue

        suggestion = await suggest_participant_for_name(message.guild, name, set(participants))
        if suggestion:
            suggestions.append(suggestion)
            continue

        unresolved.append(name)

    return list(participants.items()), unresolved, dedupe_suggestions(suggestions)

def extract_plus_names(text: str):
    return re.findall(r"(?<!\S)\+([A-Za-z0-9_.\-À-ÿ]{2,32})", text or "")

def resolve_member_by_name(guild: discord.Guild, name: str):
    target = normalize_name(name)
    for member in guild.members:
        names = [member.name, member.display_name, member.global_name or ""]
        if any(normalize_name(candidate) == target for candidate in names):
            return member
    return None

async def query_exact_member_by_name(guild: discord.Guild, name: str):
    try:
        members = await guild.query_members(name, limit=10, cache=True)
    except (discord.HTTPException, discord.Forbidden, AttributeError):
        return None

    target = normalize_name(name)
    for member in members:
        names = [member.name, member.display_name, member.global_name or ""]
        if any(normalize_name(candidate) == target for candidate in names):
            return member
    return None

async def suggest_participant_for_name(guild: discord.Guild, raw_name: str, excluded_user_ids: set[str]):
    target = normalize_name(raw_name)
    if not target:
        return None

    candidates = {}
    add_member_candidates(candidates, target, guild.members, excluded_user_ids)

    try:
        queried_members = await guild.query_members(raw_name, limit=20, cache=True)
    except (discord.HTTPException, discord.Forbidden, AttributeError):
        queried_members = []
    add_member_candidates(candidates, target, queried_members, excluded_user_ids)
    add_known_scout_candidates(candidates, target, excluded_user_ids)

    if not candidates:
        return None

    best = max(candidates.values(), key=lambda item: item["score"])
    if best["score"] < score_threshold(target):
        return None

    return {
        "raw": raw_name,
        "user_id": best["user_id"],
        "display_name": best["display_name"],
        "score": best["score"],
        "source": best["source"],
    }

def add_member_candidates(candidates: dict, target: str, members, excluded_user_ids: set[str]):
    for member in members:
        if member.bot or str(member.id) in excluded_user_ids:
            continue

        names = [member.name, member.display_name, member.global_name or ""]
        score = max(score_name_match(target, candidate) for candidate in names)
        upsert_candidate(
            candidates,
            str(member.id),
            member.display_name,
            score,
            "Discord",
        )

def add_known_scout_candidates(candidates: dict, target: str, excluded_user_ids: set[str]):
    for row in get_all_scouts():
        user_id, username = str(row[0]), row[1]
        if user_id in excluded_user_ids:
            continue
        score = score_name_match(target, username)
        upsert_candidate(candidates, user_id, username, score, "Ranking")

def upsert_candidate(candidates: dict, user_id: str, display_name: str, score: int, source: str):
    if score <= 0:
        return
    current = candidates.get(user_id)
    if current and current["score"] >= score:
        return
    candidates[user_id] = {
        "user_id": user_id,
        "display_name": display_name,
        "score": score,
        "source": source,
    }

def score_name_match(target: str, candidate: str):
    candidate = normalize_name(candidate)
    if not target or not candidate:
        return 0
    if target == candidate:
        return 100
    if candidate.startswith(target):
        return 94
    if target.startswith(candidate) and len(candidate) >= 3:
        return 90
    if len(target) >= 4 and (target in candidate or candidate in target):
        return 86
    return round(SequenceMatcher(None, target, candidate).ratio() * 100)

def score_threshold(target: str):
    if len(target) <= 3:
        return 88
    if len(target) <= 5:
        return 78
    return 72

def dedupe_suggestions(suggestions: list[dict]):
    deduped = []
    seen = set()
    for suggestion in suggestions:
        key = (suggestion["raw"].lower(), suggestion["user_id"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(suggestion)
    return deduped[:25]

def format_participant_suggestions(suggestions: list[dict]):
    lines = []
    for suggestion in suggestions[:10]:
        lines.append(
            f"`+{suggestion['raw']}` -> <@{suggestion['user_id']}> "
            f"({suggestion['score']}%, {suggestion['source']})"
        )
    if len(suggestions) > 10:
        lines.append(f"... y {len(suggestions) - 10} mas")
    return "\n".join(lines)

def build_participant_confirmation_embed(suggestions: list[dict], unresolved_names: list[str]):
    embed = discord.Embed(
        title="Confirmar participantes",
        description=(
            "Reconoci algunos nombres escritos con `+`. "
            "Marca solo las personas correctas y confirma para agregarlas a la evidencia."
        ),
        color=COLOR_WARNING,
    )
    embed.add_field(
        name="Sugerencias",
        value=format_participant_suggestions(suggestions)[:1000],
        inline=False,
    )
    if unresolved_names:
        embed.add_field(
            name="Sin coincidencia",
            value=", ".join(f"+{name}" for name in unresolved_names)[:1000],
            inline=False,
        )
    return embed

def normalize_name(name: str):
    text = unicodedata.normalize("NFKD", str(name or ""))
    return "".join(ch.lower() for ch in text if ch.isalnum())

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


if __name__ == "__main__":
    bot.run(TOKEN)
