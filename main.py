import os
import csv
import io
import asyncio
import traceback
import re
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from database import add_activity, init_db, create_evidence_review, find_scout_by_name, get_pending_evidence_message_ids, set_evidence_review_message, subtract_activity
from config import ACTIVIDADES, APPLICATION_ID, COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING, \
    DASHBOARD_CHANNEL_ID, \
    EVIDENCE_CATEGORY, EVIDENCE_CATEGORY_ID, EVIDENCE_CATEGORY_IDS, EVIDENCE_CHANNEL_IDS, \
    EVIDENCE_CHANNELS, EVIDENCE_REVIEW_CHANNEL_ID, IMAGE_EXTENSIONS
from views import DashboardView, EvidenceReviewView
from embeds import build_dashboard_embed, build_info_ranking_embed, build_perfil_embed
from permissions import is_admin
from ocr import improve_confidence_for_channel, read_message_ocr, suggest_activity_from_ocr

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# ── Bot setup ─────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, application_id=int(APPLICATION_ID))
tree = bot.tree

# Opciones de actividad para los slash commands
ACT_CHOICES = [
    app_commands.Choice(name=meta["label"], value=key)
    for key, meta in ACTIVIDADES.items()
]

# ── Eventos ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    init_db()
    bot.add_view(DashboardView())
    for message_id in get_pending_evidence_message_ids():
        bot.add_view(EvidenceReviewView(message_id))
    await tree.sync()
    print(f"✅ Bot listo: {bot.user} | Comandos sincronizados")

# ── /panel_scouts ─────────────────────────────────────────────────────────────

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    print(f"[MSG] guild={message.guild.id} channel={message.channel.id} category={message.channel.category_id} attachments={len(message.attachments)}")

    actividad = get_evidence_activity(message)
    if not actividad:
        print("[EVIDENCE] ignorado: canal/categoria no coincide")
        return

    if not has_image(message) and not message.content.strip():
        print("[EVIDENCE] ignorado: sin imagen/texto")
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
            if ocr_confidence == "Sin texto legible":
                ocr_text = ""
        except Exception as err:
            ocr_text = f"OCR error: {err}"
            print(f"[OCR ERROR] {err}")

    actividad, ocr_hits, ocr_confidence = improve_confidence_for_channel(actividad, ocr_activity, ocr_hits)

    participants, unresolved_names = await get_evidence_participants(message)

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
    image = first_image_url(message)
    if image:
        embed.set_image(url=image)

    review_msg = await review_channel.send(
        embed=embed,
        view=EvidenceReviewView(str(message.id))
    )
    set_evidence_review_message(str(message.id), str(review_msg.id))
    print(f"[EVIDENCE] enviado review={review_msg.id}")

    done_embed = discord.Embed(
        title="Evidencia enviada a revision",
        description=f"Revision: {review_msg.jump_url}",
        color=COLOR_SUCCESS
    )
    await analyzing_msg.edit(embed=done_embed)
    await asyncio.sleep(60)
    await analyzing_msg.delete()

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
        content_type = attachment.content_type or ""
        filename = attachment.filename.lower()
        if content_type.startswith("image/") or filename.endswith(IMAGE_EXTENSIONS):
            return True
    return False

def first_image_url(message: discord.Message):
    for attachment in message.attachments:
        content_type = attachment.content_type or ""
        filename = attachment.filename.lower()
        if content_type.startswith("image/") or filename.endswith(IMAGE_EXTENSIONS):
            return attachment.url
    return None

async def get_evidence_participants(message: discord.Message):
    participants = {str(message.author.id): message.author.display_name}
    unresolved = []

    for member in message.mentions:
        if not member.bot:
            participants[str(member.id)] = member.display_name

    for name in extract_plus_names(message.content):
        member = resolve_member_by_name(message.guild, name)
        if member and not member.bot:
            participants[str(member.id)] = member.display_name
            continue

        found = find_scout_by_name(name)
        if found:
            participants[str(found[0])] = found[1]
            continue

        unresolved.append(name)

    return list(participants.items()), unresolved

def extract_plus_names(text: str):
    return re.findall(r"(?<!\S)\+([A-Za-z0-9_.-]{2,32})", text or "")

def resolve_member_by_name(guild: discord.Guild, name: str):
    target = normalize_name(name)
    for member in guild.members:
        names = [member.name, member.display_name, member.global_name or ""]
        if any(normalize_name(candidate) == target for candidate in names):
            return member
    return None

def normalize_name(name: str):
    return "".join(ch.lower() for ch in name if ch.isalnum())

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

    await interaction.response.send_message("Dashboard actualizado.", ephemeral=True)

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

    await interaction.channel.send(embed=build_info_ranking_embed())
    await interaction.response.send_message("Info publicada.", ephemeral=True)


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


if __name__ == "__main__":
    bot.run(TOKEN)
