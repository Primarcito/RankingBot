import csv
import io
import asyncio
import discord
from database import add_activity, add_evidence_participants, add_scout_alias, approve_evidence, calc_puntos_totales, get_all_config, get_all_scouts, get_bot_state, get_evidence_participants, get_nivel, reject_evidence, reset_all, set_puntos, subtract_activity
from config import ACTIVIDADES, COLOR_PANEL, COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING, DASHBOARD_CHANNEL_ID
from permissions import can_review_evidence, is_admin
import participants as participant_tools

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


class InfoRankingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Mi ranking", style=discord.ButtonStyle.secondary, custom_id="info_ranking_profile", row=0)
    async def profile(self, interaction: discord.Interaction, button: discord.ui.Button):
        from embeds import build_perfil_embed

        await interaction.response.send_message(
            embed=build_perfil_embed(str(interaction.user.id), interaction.user.display_name),
            ephemeral=True,
        )

    @discord.ui.button(label="Ranking general", style=discord.ButtonStyle.primary, custom_id="info_ranking_general", row=0)
    async def ranking(self, interaction: discord.Interaction, button: discord.ui.Button):
        from embeds import build_ranking_embed

        await interaction.response.send_message(
            embed=build_ranking_embed(page=0, per_page=10),
            view=RankingPaginationView(page=0),
            ephemeral=True,
        )

    @discord.ui.button(label="Caps", style=discord.ButtonStyle.secondary, custom_id="info_ranking_caps", row=0)
    async def caps(self, interaction: discord.Interaction, button: discord.ui.Button):
        from embeds import build_priority_caps_embed

        await interaction.response.send_message(embed=build_priority_caps_embed(), ephemeral=True)


class RankingPaginationView(discord.ui.View):
    def __init__(self, page: int = 0, per_page: int = 10):
        super().__init__(timeout=180)
        self.page = page
        self.per_page = per_page
        self.update_button_states()

    @discord.ui.button(label="Anterior", style=discord.ButtonStyle.secondary, custom_id="ranking_page_prev")
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        await self.refresh(interaction)

    @discord.ui.button(label="Siguiente", style=discord.ButtonStyle.secondary, custom_id="ranking_page_next")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.total_pages() - 1, self.page + 1)
        await self.refresh(interaction)

    async def refresh(self, interaction: discord.Interaction):
        from embeds import build_ranking_embed

        self.update_button_states()
        await interaction.response.edit_message(
            embed=build_ranking_embed(page=self.page, per_page=self.per_page),
            view=self,
        )

    def update_button_states(self):
        total_pages = self.total_pages()
        self.page = min(max(0, self.page), total_pages - 1)
        for child in self.children:
            if child.custom_id == "ranking_page_prev":
                child.disabled = self.page <= 0
            elif child.custom_id == "ranking_page_next":
                child.disabled = self.page >= total_pages - 1

    def total_pages(self):
        from embeds import build_ranking_page_count

        return build_ranking_page_count(self.per_page)


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


class EvidenceAuthorConfirmView(discord.ui.View):
    def __init__(
        self,
        evidence_message_id: str,
        author_id: str,
        suggestions: list[dict],
        review_message: discord.Message,
    ):
        super().__init__(timeout=600)
        self.evidence_message_id = evidence_message_id
        self.author_id = str(author_id)
        self.suggestions = dedupe_suggestions_by_user(suggestions)
        self.selected_user_ids = {suggestion["user_id"] for suggestion in self.suggestions}
        self.review_message = review_message

        if self.suggestions:
            self.add_item(EvidenceSuggestionSelect(self.suggestions))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) == self.author_id:
            return True

        await interaction.response.send_message(
            "Solo quien subio la evidencia puede confirmar estos participantes.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="Confirmar seleccion", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        selected = [
            suggestion
            for suggestion in self.suggestions
            if suggestion["user_id"] in self.selected_user_ids
        ]
        participants = [
            (suggestion["user_id"], suggestion["display_name"])
            for suggestion in selected
        ]

        if participants and not add_evidence_participants(self.evidence_message_id, participants):
            await interaction.response.send_message("La evidencia ya fue revisada.", ephemeral=True)
            return

        if participants:
            learn_aliases_from_suggestions(selected)
            await refresh_review_participants(
                self.review_message,
                self.evidence_message_id,
                remove_suggestion_field=True,
                confirmation_text=f"Confirmado por {interaction.user.mention}",
            )
            result = "\n".join(f"<@{user_id}>" for user_id, _ in participants[:25])
        else:
            await refresh_review_participants(
                self.review_message,
                self.evidence_message_id,
                remove_suggestion_field=True,
                confirmation_text=f"Sin sugerencias confirmadas por {interaction.user.mention}",
            )
            result = "No se agrego ningun participante sugerido."

        embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
        embed.color = COLOR_SUCCESS if participants else COLOR_WARNING
        upsert_embed_field(embed, "Resultado", result[:1000])
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="No son esas", style=discord.ButtonStyle.secondary)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await refresh_review_participants(
            self.review_message,
            self.evidence_message_id,
            remove_suggestion_field=True,
            confirmation_text=f"Sugerencias descartadas por {interaction.user.mention}",
        )
        embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
        embed.color = COLOR_WARNING
        upsert_embed_field(
            embed,
            "Resultado",
            "No se agregaron sugerencias. El equipo de revision puede corregirlo manualmente.",
        )
        await interaction.response.edit_message(embed=embed, view=None)


class EvidenceSuggestionSelect(discord.ui.Select):
    def __init__(self, suggestions: list[dict]):
        options = []
        for suggestion in suggestions[:25]:
            raw_names = ", ".join(f"+{name}" for name in suggestion["raw_names"][:3])
            label = truncate_text(f"{raw_names} -> {suggestion['display_name']}", 100)
            description = truncate_text(
                f"{suggestion['score']}% coincidencia desde {suggestion['source']}",
                100,
            )
            options.append(
                discord.SelectOption(
                    label=label,
                    value=suggestion["user_id"],
                    description=description,
                    default=True,
                )
            )

        super().__init__(
            placeholder="Desmarca personas si no corresponden",
            min_values=0,
            max_values=max(1, len(options)),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        self.view.selected_user_ids = set(self.values)
        await interaction.response.defer()


class EvidenceReviewerSuggestionConfirmView(discord.ui.View):
    def __init__(self, evidence_message_id: str, review_message: discord.Message, suggestions: list[dict]):
        super().__init__(timeout=120)
        self.evidence_message_id = evidence_message_id
        self.review_message = review_message
        self.suggestions = dedupe_suggestions_by_user(suggestions)
        self.selected_user_ids = {suggestion["user_id"] for suggestion in self.suggestions}
        self.add_item(EvidenceSuggestionSelect(self.suggestions))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if can_review_evidence(interaction):
            return True
        await interaction.response.send_message("No tienes permiso.", ephemeral=True)
        return False

    @discord.ui.button(label="Agregar seleccion", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        selected = [
            suggestion
            for suggestion in self.suggestions
            if suggestion["user_id"] in self.selected_user_ids
        ]
        participants = [
            (suggestion["user_id"], suggestion["display_name"])
            for suggestion in selected
        ]

        if not participants:
            await interaction.response.edit_message(content="No se agrego ninguna sugerencia.", embed=None, view=None)
            return

        if not add_evidence_participants(self.evidence_message_id, participants):
            await interaction.response.send_message("Evidencia ya revisada.", ephemeral=True)
            return

        learn_aliases_from_suggestions(selected)
        await refresh_review_participants(
            self.review_message,
            self.evidence_message_id,
            confirmation_text=f"Sugerencias agregadas por {interaction.user.mention}",
        )
        embed = discord.Embed(
            title="Participantes agregados",
            description="\n".join(f"<@{user_id}>" for user_id, _ in participants[:25]),
            color=COLOR_SUCCESS,
        )
        await interaction.response.edit_message(embed=embed, view=None)


class EvidenceReviewView(discord.ui.View):
    def __init__(self, evidence_message_id: str):
        super().__init__(timeout=None)
        self.evidence_message_id = evidence_message_id
        self.add_item(EvidenceAddByTextButton(evidence_message_id))
        self.add_item(EvidenceAddParticipantsButton(evidence_message_id))
        self.add_item(EvidenceApproveButton(evidence_message_id))
        self.add_item(EvidenceRejectButton(evidence_message_id))


class EvidenceAddByTextButton(discord.ui.Button):
    def __init__(self, evidence_message_id: str):
        super().__init__(
            label="Agregar por texto",
            style=discord.ButtonStyle.secondary,
            custom_id=f"evidence_add_text:{evidence_message_id}"
        )
        self.evidence_message_id = evidence_message_id

    async def callback(self, interaction: discord.Interaction):
        if not can_review_evidence(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return

        await interaction.response.send_modal(
            EvidenceParticipantTextModal(self.evidence_message_id, interaction.message)
        )


class EvidenceParticipantTextModal(discord.ui.Modal):
    nombres = discord.ui.TextInput(
        label="Nombres, menciones o IDs",
        placeholder="+violeth +chino +peccato +shourout +sherlock +littleponny",
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    def __init__(self, evidence_message_id: str, review_message: discord.Message):
        super().__init__(title="Agregar participantes")
        self.evidence_message_id = evidence_message_id
        self.review_message = review_message

    async def on_submit(self, interaction: discord.Interaction):
        existing_user_ids = {user_id for user_id, _ in get_evidence_participants(self.evidence_message_id)}
        participants, unresolved, suggestions = await participant_tools.resolve_manual_names(
            interaction.guild,
            str(self.nombres.value),
            existing_user_ids,
        )

        added = []
        if participants:
            if not add_evidence_participants(self.evidence_message_id, participants):
                await interaction.response.send_message("Evidencia ya revisada.", ephemeral=True)
                return
            added = participants
            await refresh_review_participants(self.review_message, self.evidence_message_id)

        embed = build_participant_resolution_embed(added, suggestions, unresolved)
        view = None
        if suggestions:
            view = EvidenceReviewerSuggestionConfirmView(
                self.evidence_message_id,
                self.review_message,
                suggestions,
            )

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class EvidenceAddParticipantsButton(discord.ui.Button):
    def __init__(self, evidence_message_id: str):
        super().__init__(
            label="Agregar por selector",
            style=discord.ButtonStyle.secondary,
            custom_id=f"evidence_add_people:{evidence_message_id}"
        )
        self.evidence_message_id = evidence_message_id

    async def callback(self, interaction: discord.Interaction):
        if not can_review_evidence(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Agregar personas",
            description="Selecciona los usuarios que tambien reciben esta evidencia.",
            color=COLOR_PANEL
        )
        await interaction.response.send_message(
            embed=embed,
            view=EvidenceParticipantSelectView(self.evidence_message_id, interaction.message),
            ephemeral=True
        )


class EvidenceParticipantSelectView(discord.ui.View):
    def __init__(self, evidence_message_id: str, review_message: discord.Message):
        super().__init__(timeout=60)
        self.add_item(EvidenceParticipantSelect(evidence_message_id, review_message))


class EvidenceParticipantSelect(discord.ui.UserSelect):
    def __init__(self, evidence_message_id: str, review_message: discord.Message):
        super().__init__(
            placeholder="Arroba/agrega participantes",
            min_values=1,
            max_values=25,
        )
        self.evidence_message_id = evidence_message_id
        self.review_message = review_message

    async def callback(self, interaction: discord.Interaction):
        participants = [
            (str(user.id), getattr(user, "display_name", None) or user.global_name or user.name)
            for user in self.values
            if not user.bot
        ]
        if not participants:
            await interaction.response.send_message("No selecciones bots.", ephemeral=True)
            return

        if not add_evidence_participants(self.evidence_message_id, participants):
            await interaction.response.send_message("Evidencia ya revisada.", ephemeral=True)
            return

        await refresh_review_participants(self.review_message, self.evidence_message_id)
        await interaction.response.send_message("Personas agregadas.", ephemeral=True)


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
        await set_review_reaction(interaction, "\N{WHITE HEAVY CHECK MARK}")
        await set_source_reaction(interaction, "\N{WHITE HEAVY CHECK MARK}")
        asyncio.create_task(delete_scouteo_dashboard_after_delay(interaction, self.evidence_message_id))


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
        await set_review_reaction(interaction, "\N{CROSS MARK}")
        await set_source_reaction(interaction, "\N{CROSS MARK}")


async def set_review_reaction(interaction: discord.Interaction, emoji: str):
    try:
        await interaction.message.clear_reaction("\N{WHITE HEAVY CHECK MARK}")
        await interaction.message.clear_reaction("\N{CROSS MARK}")
        await interaction.message.add_reaction(emoji)
    except discord.HTTPException:
        pass


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


async def delete_scouteo_dashboard_after_delay(interaction: discord.Interaction, evidence_message_id: str):
    dashboard_ref = get_bot_state(f"scouteo_count_dashboard:{evidence_message_id}")
    if not dashboard_ref or ":" not in dashboard_ref:
        return

    await asyncio.sleep(3600)

    channel_id, message_id = dashboard_ref.split(":", 1)
    try:
        channel = interaction.client.get_channel(int(channel_id)) or await interaction.client.fetch_channel(int(channel_id))
        message = await channel.fetch_message(int(message_id))
        await message.delete()
    except (discord.HTTPException, ValueError, TypeError, AttributeError):
        pass


def upsert_embed_field(embed: discord.Embed, name: str, value: str):
    for index, field in enumerate(embed.fields):
        if field.name == name:
            embed.set_field_at(index, name=name, value=value, inline=False)
            return
    embed.add_field(name=name, value=value, inline=False)


def build_participant_resolution_embed(added, suggestions: list[dict], unresolved: list[str]):
    embed = discord.Embed(title="Resolucion de participantes", color=COLOR_PANEL)
    if added:
        embed.add_field(
            name="Agregados",
            value="\n".join(f"<@{user_id}>" for user_id, _ in added[:25]),
            inline=False,
        )
    if suggestions:
        embed.add_field(
            name="Sugerencias",
            value=participant_tools.format_participant_suggestions(suggestions)[:1000],
            inline=False,
        )
    if unresolved:
        embed.add_field(
            name="Sin coincidencia",
            value=", ".join(f"`+{name}`" for name in unresolved[:25])[:1000],
            inline=False,
        )
    if not added and not suggestions and not unresolved:
        embed.description = "No encontre nombres nuevos para agregar."
    return embed


def learn_aliases_from_suggestions(suggestions: list[dict]):
    for suggestion in suggestions:
        for raw_name in suggestion.get("raw_names", [suggestion.get("raw", "")]):
            add_scout_alias(
                suggestion["user_id"],
                suggestion["display_name"],
                raw_name,
            )


def remove_embed_field(embed: discord.Embed, name: str):
    for index, field in enumerate(embed.fields):
        if field.name == name:
            embed.remove_field(index)
            return


async def refresh_review_participants(
    review_message: discord.Message,
    evidence_message_id: str,
    remove_suggestion_field: bool = False,
    confirmation_text: str | None = None,
):
    embed = review_message.embeds[0]
    rows = get_evidence_participants(evidence_message_id)
    value = "\n".join(f"<@{user_id}>" for user_id, _ in rows[:25]) or "Sin participantes"
    upsert_embed_field(embed, "Participantes", value[:1000])
    if remove_suggestion_field:
        remove_embed_field(embed, "Sugerencias por confirmar")
    if confirmation_text:
        upsert_embed_field(embed, "Confirmacion participantes", confirmation_text[:1000])
    await review_message.edit(embed=embed)


def dedupe_suggestions_by_user(suggestions: list[dict]):
    grouped = {}
    for suggestion in suggestions:
        user_id = str(suggestion["user_id"])
        current = grouped.get(user_id)
        if not current:
            grouped[user_id] = {
                "user_id": user_id,
                "display_name": suggestion["display_name"],
                "score": suggestion["score"],
                "source": suggestion["source"],
                "raw_names": [suggestion["raw"]],
            }
            continue

        current["raw_names"].append(suggestion["raw"])
        if suggestion["score"] > current["score"]:
            current["score"] = suggestion["score"]
            current["source"] = suggestion["source"]
            current["display_name"] = suggestion["display_name"]
    return list(grouped.values())[:25]


def truncate_text(text: str, max_len: int):
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


async def refresh_dashboard_message(interaction: discord.Interaction):
    if not DASHBOARD_CHANNEL_ID:
        return

    from embeds import build_dashboard_embed

    channel = interaction.client.get_channel(DASHBOARD_CHANNEL_ID)
    if not channel:
        try:
            channel = await interaction.client.fetch_channel(DASHBOARD_CHANNEL_ID)
        except discord.HTTPException:
            return

    async for msg in channel.history(limit=20):
        if msg.author.id == interaction.client.user.id and msg.embeds and "Dashboard Scouts" in (msg.embeds[0].title or ""):
            await msg.edit(embed=build_dashboard_embed(), view=DashboardView())
            return

