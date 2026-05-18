import discord
from database import add_activity, approve_evidence, reject_evidence, reset_all
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

