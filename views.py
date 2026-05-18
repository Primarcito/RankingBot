import csv
import io
import discord
from database import add_activity, approve_evidence, calc_puntos_totales, get_all_config, get_all_scouts, get_nivel, reject_evidence, reset_all, set_puntos, subtract_activity
from config import ACTIVIDADES, COLOR_PANEL, COLOR_SUCCESS, COLOR_ERROR
from permissions import can_review_evidence, is_admin

# ── Panel de actividades ──────────────────────────────────────────────────────

class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # persistente
        for act_key, meta in ACTIVIDADES.items():
            self.add_item(ActivityButton(act_key, meta["label"], meta["emoji"]))
        self.add_item(RankingButton())


class ActivityButton(discord.ui.Button):
    def __init__(self, actividad: str, label: str, emoji: str):
        super().__init__(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.primary,
            custom_id=f"scout_{actividad}"
        )
        self.actividad = actividad

    async def callback(self, interaction: discord.Interaction):
        user = interaction.user
        pts = add_activity(str(user.id), user.display_name, self.actividad, 1)
        embed = discord.Embed(
            description=f"**{ACTIVIDADES[self.actividad]['emoji']} {ACTIVIDADES[self.actividad]['label']}** registrado para **{user.display_name}**\n`+1 actividad  |  +{pts} pts`",
            color=COLOR_SUCCESS
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class RankingButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Ver Ranking",
            emoji="🏆",
            style=discord.ButtonStyle.secondary,
            custom_id="scout_ranking"
        )

    async def callback(self, interaction: discord.Interaction):
        from embeds import build_ranking_embed
        embed = build_ranking_embed()
        await interaction.response.send_message(embed=embed, ephemeral=True)


class PointsSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        for key, meta in ACTIVIDADES.items():
            self.add_item(PointsActivityButton(key, meta))


class PointsActivityButton(discord.ui.Button):
    def __init__(self, key: str, meta: dict):
        points_config = {activity: points for activity, points in get_all_config()}
        super().__init__(
            label=f"{meta['label']} ({points_config.get(key, 0)} pts)",
            emoji=meta["emoji"],
            style=discord.ButtonStyle.secondary,
        )
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(PointsValueModal(self.key))


class PointsValueModal(discord.ui.Modal):
    puntos = discord.ui.TextInput(
        label="Nuevos puntos",
        placeholder="Ej: 5",
        max_length=4,
    )

    def __init__(self, actividad_key: str):
        meta = ACTIVIDADES[actividad_key]
        super().__init__(title=f"{meta['emoji']} {meta['label']} — nuevo valor")
        self.actividad_key = actividad_key

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(str(self.puntos.value).strip())
            if amount < 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ Ingresa un número válido mayor a 0.", ephemeral=True)
            return

        set_puntos(self.actividad_key, amount)
        meta = ACTIVIDADES[self.actividad_key]
        embed = discord.Embed(
            description=f"{meta['emoji']} **{meta['label']}** ahora vale **{amount} pts**.",
            color=COLOR_SUCCESS
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await refresh_dashboard_message(interaction)


# ── Sumar / Restar: selección de actividad ────────────────────────────────────

class ActivitySelectView(discord.ui.View):
    def __init__(self, action: str):
        """action: 'sumar' | 'restar'"""
        super().__init__(timeout=60)
        self.action = action
        for key, meta in ACTIVIDADES.items():
            self.add_item(ActivitySelectButton(key, meta, action))


class ActivitySelectButton(discord.ui.Button):
    def __init__(self, key: str, meta: dict, action: str):
        style = discord.ButtonStyle.success if action == "sumar" else discord.ButtonStyle.danger
        super().__init__(
            label=meta["label"],
            emoji=meta["emoji"],
            style=style,
        )
        self.key = key
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ActivityValueModal(self.key, self.action))


class ActivityValueModal(discord.ui.Modal):
    user_id  = discord.ui.TextInput(label="User ID del scout", placeholder="Clic derecho → Copiar ID de usuario")
    cantidad = discord.ui.TextInput(label="Cantidad", placeholder="Ej: 1", max_length=4)

    def __init__(self, actividad_key: str, action: str):
        meta = ACTIVIDADES[actividad_key]
        verb = "Sumar" if action == "sumar" else "Restar"
        super().__init__(title=f"{verb} — {meta['emoji']} {meta['label']}")
        self.actividad_key = actividad_key
        self.action = action

    async def on_submit(self, interaction: discord.Interaction):
        uid = str(self.user_id.value).strip()
        try:
            amount = int(str(self.cantidad.value).strip())
            if amount <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ Cantidad inválida.", ephemeral=True)
            return

        member = interaction.guild.get_member(int(uid)) if uid.isdigit() else None
        username = member.display_name if member else uid
        meta = ACTIVIDADES[self.actividad_key]

        if self.action == "sumar":
            pts = add_activity(uid, username, self.actividad_key, amount)
            embed = discord.Embed(
                description=f"✅ **+{amount}** {meta['emoji']} {meta['label']} a **{username}**  →  `+{pts} pts`",
                color=COLOR_SUCCESS
            )
        else:
            pts = subtract_activity(uid, username, self.actividad_key, amount)
            embed = discord.Embed(
                description=f"✅ **-{amount}** {meta['emoji']} {meta['label']} a **{username}**  →  `-{pts} pts`",
                color=COLOR_ERROR
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)
        await refresh_dashboard_message(interaction)


# ── DashboardView actualizado ─────────────────────────────────────────────────

class DashboardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Ranking", style=discord.ButtonStyle.secondary, custom_id="dash_ranking", row=0)
    async def ranking(self, interaction: discord.Interaction, button: discord.ui.Button):
        from embeds import build_ranking_embed
        await interaction.response.send_message(embed=build_ranking_embed(), ephemeral=True)

    @discord.ui.button(label="Mi perfil", style=discord.ButtonStyle.secondary, custom_id="dash_profile", row=0)
    async def profile(self, interaction: discord.Interaction, button: discord.ui.Button):
        from embeds import build_perfil_embed
        await interaction.response.send_message(
            embed=build_perfil_embed(str(interaction.user.id), interaction.user.display_name),
            ephemeral=True
        )

    @discord.ui.button(label="Config pts", style=discord.ButtonStyle.primary, custom_id="dash_points", row=0)
    async def points(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Sin permiso.", ephemeral=True)
            return
        embed = discord.Embed(
            title="⚙️ Configurar puntos",
            description="Elige la actividad que quieres modificar:",
            color=COLOR_PANEL
        )
        await interaction.response.send_message(embed=embed, view=PointsSelectView(), ephemeral=True)

    @discord.ui.button(label="Sumar", style=discord.ButtonStyle.success, custom_id="dash_add", row=0)
    async def add(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Sin permiso.", ephemeral=True)
            return
        embed = discord.Embed(
            title="➕ Sumar actividad",
            description="Elige la actividad a sumar:",
            color=COLOR_SUCCESS
        )
        await interaction.response.send_message(embed=embed, view=ActivitySelectView("sumar"), ephemeral=True)

    @discord.ui.button(label="Restar", style=discord.ButtonStyle.danger, custom_id="dash_subtract", row=0)
    async def subtract(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Sin permiso.", ephemeral=True)
            return
        embed = discord.Embed(
            title="➖ Restar actividad",
            description="Elige la actividad a restar:",
            color=COLOR_ERROR
        )
        await interaction.response.send_message(embed=embed, view=ActivitySelectView("restar"), ephemeral=True)

    @discord.ui.button(label="Exportar", style=discord.ButtonStyle.secondary, custom_id="dash_export", row=1)
    async def export(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Sin permiso.", ephemeral=True)
            return
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["user_id", "username", *ACTIVIDADES.keys(), "total_puntos", "nivel", "beneficio"])
        for row in get_all_scouts():
            pts = calc_puntos_totales(row)
            nivel, beneficio = get_nivel(pts)
            writer.writerow([*row, pts, nivel, beneficio])
        output.seek(0)
        file = discord.File(fp=io.BytesIO(output.getvalue().encode()), filename="scouts_export.csv")
        await interaction.response.send_message(file=file, ephemeral=True)

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.danger, custom_id="dash_reset", row=1)
    async def reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Sin permiso.", ephemeral=True)
            return
        embed = discord.Embed(
            title="⚠️ Confirmar reset",
            description="Esto borra **todos** los conteos de scouts. Esta acción no se puede deshacer.",
            color=COLOR_ERROR
        )
        await interaction.response.send_message(embed=embed, view=ResetView(), ephemeral=True)


# ── Confirmación de Reset ─────────────────────────────────────────────────────

class ResetView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)

    @discord.ui.button(label="✅ Confirmar Reset", style=discord.ButtonStyle.danger, custom_id="reset_confirm")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
            return
        reset_all()
        embed = discord.Embed(description="✅ Todos los conteos han sido reseteados.", color=COLOR_SUCCESS)
        await interaction.response.edit_message(embed=embed, view=None)


    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary, custom_id="reset_cancel")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(description="Reset cancelado.", color=COLOR_ERROR)
        await interaction.response.edit_message(embed=embed, view=None)


class EvidenceReviewView(discord.ui.View):
    def __init__(self, evidence_message_id: str):
        super().__init__(timeout=None)
        self.evidence_message_id = evidence_message_id
        self.add_item(EvidenceApproveButton(evidence_message_id))
        self.add_item(EvidenceRejectButton(evidence_message_id))


class EvidenceApproveButton(discord.ui.Button):
    def __init__(self, evidence_message_id: str):
        super().__init__(
            label="Aprobar",
            style=discord.ButtonStyle.success,
            custom_id=f"evidence_approve:{evidence_message_id}"
        )
        self.evidence_message_id = evidence_message_id

    async def callback(self, interaction: discord.Interaction):
        if not can_review_evidence(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return

        result = approve_evidence(self.evidence_message_id)
        if not result:
            await interaction.response.send_message("Ya fue revisado.", ephemeral=True)
            return

        embed = interaction.message.embeds[0]
        embed.color = COLOR_SUCCESS
        embed.add_field(name="Estado", value=f"Aprobado por {interaction.user.mention}", inline=False)
        await interaction.response.edit_message(embed=embed, view=None)
        await set_source_reaction(interaction, "\N{WHITE HEAVY CHECK MARK}")


class EvidenceRejectButton(discord.ui.Button):
    def __init__(self, evidence_message_id: str):
        super().__init__(
            label="Rechazar",
            style=discord.ButtonStyle.danger,
            custom_id=f"evidence_reject:{evidence_message_id}"
        )
        self.evidence_message_id = evidence_message_id

    async def callback(self, interaction: discord.Interaction):
        if not can_review_evidence(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return

        if not reject_evidence(self.evidence_message_id):
            await interaction.response.send_message("Ya fue revisado.", ephemeral=True)
            return

        embed = interaction.message.embeds[0]
        embed.color = COLOR_ERROR
        embed.add_field(name="Estado", value=f"Rechazado por {interaction.user.mention}", inline=False)
        await interaction.response.edit_message(embed=embed, view=None)
        await set_source_reaction(interaction, "\N{CROSS MARK}")


async def set_source_reaction(interaction: discord.Interaction, emoji: str):
    try:
        source_url = interaction.message.embeds[0].description.split("[Abrir evidencia](")[-1].split(")")[0]
        parts = source_url.rstrip("/").split("/")
        channel_id = int(parts[-2])
        source_message_id = int(parts[-1])
        channel = interaction.client.get_channel(channel_id) or await interaction.client.fetch_channel(channel_id)
        source_message = await channel.fetch_message(source_message_id)
        await source_message.clear_reaction("\N{HOURGLASS}")
        await source_message.clear_reaction("\N{OUTBOX TRAY}")
        await source_message.add_reaction(emoji)
    except (discord.HTTPException, IndexError, ValueError, AttributeError):
        pass

