import os
import csv
import io
import asyncio
import traceback
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from database import init_db, get_all_scouts, calc_puntos_totales, get_nivel, \
    add_activity, subtract_activity, set_puntos, get_puntos, ensure_scout, COLS, \
    create_evidence_review, get_pending_evidence_message_ids, set_evidence_review_message
from config import ACTIVIDADES, APPLICATION_ID, COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING, \
    EVIDENCE_CATEGORY, EVIDENCE_CATEGORY_ID, EVIDENCE_CATEGORY_IDS, EVIDENCE_CHANNEL_IDS, \
    EVIDENCE_CHANNELS, EVIDENCE_REVIEW_CHANNEL_ID, IMAGE_EXTENSIONS
from views import EvidenceReviewView, PanelView, ResetView
from embeds import build_panel_embed, build_ranking_embed, build_perfil_embed
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
    bot.add_view(PanelView())   # re-registrar vista persistente tras reinicio
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

    ocr_text = ""
    ocr_activity = None
    ocr_hits = []
    ocr_confidence = "Sin OCR"
    if has_image(message):
        try:
            ocr_text = await read_message_ocr(message)
            ocr_activity, ocr_hits, ocr_confidence = suggest_activity_from_ocr(ocr_text)
        except Exception as err:
            ocr_text = f"OCR error: {err}"
            print(f"[OCR ERROR] {err}")

    actividad, ocr_hits, ocr_confidence = improve_confidence_for_channel(actividad, ocr_activity, ocr_hits)

    pts = create_evidence_review(
        str(message.id),
        str(message.author.id),
        message.author.display_name,
        actividad
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

    try:
        await message.add_reaction("⏳")
    except discord.HTTPException:
        pass

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

@tree.command(name="panel_scouts", description="Muestra el panel de registro de actividades")
async def panel_scouts(interaction: discord.Interaction):
    embed = build_panel_embed()
    view = PanelView()
    await interaction.response.send_message(embed=embed, view=view)

# ── /ranking_scouts ───────────────────────────────────────────────────────────

@tree.command(name="ranking_scouts", description="Muestra el ranking de scouts")
async def ranking_scouts(interaction: discord.Interaction):
    embed = build_ranking_embed()
    await interaction.response.send_message(embed=embed)

# ── /perfil_scout ─────────────────────────────────────────────────────────────

@tree.command(name="perfil_scout", description="Muestra tu perfil de scout")
async def perfil_scout(interaction: discord.Interaction):
    user = interaction.user
    embed = build_perfil_embed(str(user.id), user.display_name)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ── /perfil_scout_usuario ─────────────────────────────────────────────────────

@tree.command(name="perfil_scout_usuario", description="Muestra el perfil de otro scout")
@app_commands.describe(usuario="Usuario a consultar")
async def perfil_scout_usuario(interaction: discord.Interaction, usuario: discord.Member):
    embed = build_perfil_embed(str(usuario.id), usuario.display_name)
    await interaction.response.send_message(embed=embed)

# ── /set_puntos ───────────────────────────────────────────────────────────────

@tree.command(name="set_puntos", description="[Admin] Modifica el puntaje de una actividad")
@app_commands.describe(actividad="Actividad a modificar", cantidad="Nuevos puntos")
@app_commands.choices(actividad=ACT_CHOICES)
async def set_puntos_cmd(interaction: discord.Interaction, actividad: app_commands.Choice[str], cantidad: int):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return
    set_puntos(actividad.value, cantidad)
    embed = discord.Embed(
        description=f"✅ **{actividad.name}** ahora vale `{cantidad} pts`",
        color=COLOR_SUCCESS
    )
    await interaction.response.send_message(embed=embed)

# ── /sumar_scout ──────────────────────────────────────────────────────────────

@tree.command(name="sumar_scout", description="[Admin] Suma actividades a un scout manualmente")
@app_commands.describe(usuario="Scout objetivo", actividad="Actividad", cantidad="Cantidad a sumar")
@app_commands.choices(actividad=ACT_CHOICES)
async def sumar_scout(interaction: discord.Interaction, usuario: discord.Member,
                      actividad: app_commands.Choice[str], cantidad: int):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return
    if cantidad <= 0:
        await interaction.response.send_message("La cantidad debe ser mayor a 0.", ephemeral=True)
        return
    pts = add_activity(str(usuario.id), usuario.display_name, actividad.value, cantidad)
    embed = discord.Embed(
        description=f"✅ `+{cantidad}` **{actividad.name}** a **{usuario.display_name}** (`+{pts} pts`)",
        color=COLOR_SUCCESS
    )
    await interaction.response.send_message(embed=embed)

# ── /restar_scout ─────────────────────────────────────────────────────────────

@tree.command(name="restar_scout", description="[Admin] Resta actividades a un scout manualmente")
@app_commands.describe(usuario="Scout objetivo", actividad="Actividad", cantidad="Cantidad a restar")
@app_commands.choices(actividad=ACT_CHOICES)
async def restar_scout(interaction: discord.Interaction, usuario: discord.Member,
                       actividad: app_commands.Choice[str], cantidad: int):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return
    if cantidad <= 0:
        await interaction.response.send_message("La cantidad debe ser mayor a 0.", ephemeral=True)
        return
    pts = subtract_activity(str(usuario.id), usuario.display_name, actividad.value, cantidad)
    embed = discord.Embed(
        description=f"✅ `-{cantidad}` **{actividad.name}** a **{usuario.display_name}** (`-{pts} pts`)",
        color=COLOR_WARNING
    )
    await interaction.response.send_message(embed=embed)

# ── /reset_scouts ─────────────────────────────────────────────────────────────

@tree.command(name="reset_scouts", description="[Admin] Resetea todos los conteos")
async def reset_scouts(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return
    embed = discord.Embed(
        title="⚠️ Confirmar Reset",
        description="¿Seguro que deseas borrar **todos** los conteos? Esta acción es irreversible.",
        color=COLOR_ERROR
    )
    await interaction.response.send_message(embed=embed, view=ResetView(), ephemeral=True)

# ── /exportar_scouts ──────────────────────────────────────────────────────────

@tree.command(name="exportar_scouts", description="[Admin] Exporta los datos de scouts a CSV")
async def exportar_scouts(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=False)
    scouts = get_all_scouts()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["user_id","username","kill_scout","kill_pelea","limpieza_aspecto",
                     "scouteo","mapeo","total_puntos","nivel","beneficio"])

    for row in scouts:
        pts = calc_puntos_totales(row)
        nivel, beneficio = get_nivel(pts)
        writer.writerow([row[0], row[1], row[2], row[3], row[4], row[5], row[6],
                         pts, nivel, beneficio])

    output.seek(0)
    file = discord.File(fp=io.BytesIO(output.getvalue().encode()), filename="scouts_export.csv")
    embed = discord.Embed(description="✅ Exportación generada.", color=COLOR_SUCCESS)
    await interaction.followup.send(embed=embed, file=file)

# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(TOKEN)
