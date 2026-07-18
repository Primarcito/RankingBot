import asyncio
from datetime import datetime, timedelta, timezone
import discord
from database import (
    add_evidence_participants,
    add_scout_alias,
    approve_evidence,
    create_ranking_snapshot,
    get_all_config,
    get_bot_state,
    get_evidence_participants,
    get_evidence_summary,
    get_puntos,
    get_scouteo_review_rows,
    record_audit_event,
    reject_evidence,
    reset_all,
    set_puntos,
    set_scouteo_review_multiplier,
)
from config import (
    ACTIVIDADES,
    AUTO_RESET_HOUR_UTC,
    AUTO_RESET_MINUTE_UTC,
    AUTO_RESET_WEEKDAY_UTC,
    COLOR_PANEL,
    COLOR_SUCCESS,
    COLOR_ERROR,
    COLOR_WARNING,
    DASHBOARD_CHANNEL_ID,
    INFO_RANKING_CHANNEL_ID,
)
from permissions import can_review_evidence, is_gm_member
from emojis import button_emoji, reaction_emoji, reaction_variants, text_emoji
import participants as participant_tools


def record_view_audit(
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


def record_participants_audit(
    interaction: discord.Interaction,
    evidence_message_id: str,
    participants: list[tuple],
    source: str,
):
    if not participants:
        return
    record_view_audit(
        interaction,
        "participantes",
        "agregar_a_evidencia",
        target_type="evidencia",
        target_id=evidence_message_id,
        summary=f"Agrego {len(participants)} participante(s) mediante {source}.",
        details={
            "origen": source,
            "participantes": ", ".join(f"{name} ({user_id})" for user_id, name, *_ in participants),
        },
    )


class PointsSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        for key, meta in ACTIVIDADES.items():
            self.add_item(PointsActivityButton(key, meta))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if is_gm_member(interaction.user):
            return True
        await interaction.response.send_message("Esta configuracion requiere jerarquia GM / Lider.", ephemeral=True)
        return False


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
        super().__init__(title=f"Configurar puntos · {meta['label']}")
        self.actividad_key = actividad_key

    async def on_submit(self, interaction: discord.Interaction):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Esta configuracion requiere jerarquia GM / Lider.", ephemeral=True)
            return
        try:
            amount = int(str(self.puntos.value).strip())
            if amount < 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                f"{text_emoji('REJECTED')} Ingresa un número válido mayor a 0.",
                ephemeral=True,
            )
            return

        previous = get_puntos(self.actividad_key)
        set_puntos(self.actividad_key, amount)
        meta = ACTIVIDADES[self.actividad_key]
        record_view_audit(
            interaction,
            "configuracion",
            "cambiar_valor_actividad",
            target_type="actividad",
            target_id=self.actividad_key,
            summary=f"{meta['label']}: {previous} -> {amount} puntos por unidad.",
            details={"antes": previous, "despues": amount},
        )
        embed = discord.Embed(
            description=f"{meta['emoji']} **{meta['label']}** ahora vale **{amount} pts**.",
            color=COLOR_SUCCESS
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await refresh_public_messages(interaction)

# ── DashboardView actualizado ─────────────────────────────────────────────────

class DashboardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Ranking",
        emoji=button_emoji("RANKING"),
        style=discord.ButtonStyle.primary,
        custom_id="dash_ranking",
        row=0,
    )
    async def ranking(self, interaction: discord.Interaction, button: discord.ui.Button):
        from embeds import build_ranking_embed
        await interaction.response.send_message(embed=build_ranking_embed(), ephemeral=True)

    @discord.ui.button(
        label="Perfil",
        emoji=button_emoji("SCOUT"),
        style=discord.ButtonStyle.secondary,
        custom_id="dash_profile",
        row=0,
    )
    async def profile(self, interaction: discord.Interaction, button: discord.ui.Button):
        from embeds import build_perfil_embed
        await interaction.response.send_message(
            embed=build_perfil_embed(str(interaction.user.id), interaction.user.display_name),
            ephemeral=True
        )

    @discord.ui.button(
        label="Prio",
        emoji=button_emoji("PRIO"),
        style=discord.ButtonStyle.secondary,
        custom_id="dash_prio_requirement",
        row=0,
    )
    async def priority_requirement(self, interaction: discord.Interaction, button: discord.ui.Button):
        from embeds import build_priority_requirement_embed

        await interaction.response.send_message(
            embed=build_priority_requirement_embed(),
            ephemeral=True,
        )


class InfoRankingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Perfil",
        emoji=button_emoji("SCOUT"),
        style=discord.ButtonStyle.secondary,
        custom_id="info_ranking_profile",
        row=0,
    )
    async def profile(self, interaction: discord.Interaction, button: discord.ui.Button):
        from embeds import build_perfil_embed

        await interaction.response.send_message(
            embed=build_perfil_embed(str(interaction.user.id), interaction.user.display_name),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Ranking",
        emoji=button_emoji("RANKING"),
        style=discord.ButtonStyle.primary,
        custom_id="info_ranking_general",
        row=0,
    )
    async def ranking(self, interaction: discord.Interaction, button: discord.ui.Button):
        from embeds import build_ranking_embed

        await interaction.response.send_message(
            embed=build_ranking_embed(page=0, per_page=10),
            view=RankingPaginationView(page=0),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Prio",
        emoji=button_emoji("PRIO"),
        style=discord.ButtonStyle.secondary,
        custom_id="info_ranking_caps",
        row=0,
    )
    async def caps(self, interaction: discord.Interaction, button: discord.ui.Button):
        from embeds import build_priority_requirement_embed

        await interaction.response.send_message(embed=build_priority_requirement_embed(), ephemeral=True)


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

    @discord.ui.button(
        label="Confirmar",
        emoji=button_emoji("CALENDAR"),
        style=discord.ButtonStyle.danger,
        custom_id="reset_confirm",
    )
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_gm_member(interaction.user):
            await interaction.response.send_message("Esta accion requiere jerarquia GM / Lider.", ephemeral=True)
            return
        snapshot_id = create_ranking_snapshot(current_weekly_ranking_start(), datetime.now(timezone.utc), "dashboard_reset")
        reset_all()
        record_view_audit(
            interaction,
            "cierres",
            "cierre_manual",
            target_type="cierre",
            target_id=snapshot_id,
            summary=(
                f"Archivo el ranking en el cierre #{snapshot_id} y limpio el periodo actual."
                if snapshot_id else
                "Limpio el periodo actual; no habia puntos para archivar."
            ),
            details={"snapshot_id": snapshot_id},
        )
        snapshot_text = f" Cierre guardado: `#{snapshot_id}`." if snapshot_id else " No habia puntos para archivar."
        embed = discord.Embed(
            description=f"{text_emoji('APPROVED')} Todos los conteos han sido reseteados.{snapshot_text}",
            color=COLOR_SUCCESS,
        )
        await interaction.response.edit_message(embed=embed, view=None)
        await refresh_public_messages(interaction)


    @discord.ui.button(
        label="Cancelar",
        emoji=button_emoji("REJECTED"),
        style=discord.ButtonStyle.secondary,
        custom_id="reset_cancel",
    )
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(description="Reset cancelado.", color=COLOR_ERROR)
        await interaction.response.edit_message(embed=embed, view=None)


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


class EvidenceAuthorConfirmView(discord.ui.View):
    def __init__(
        self,
        evidence_message_id: str,
        author_id: str,
        suggestions: list[dict],
        review_message: discord.Message,
    ):
        super().__init__(timeout=86400)
        self.evidence_message_id = evidence_message_id
        self.author_id = str(author_id)
        self.suggestions = dedupe_suggestions_by_user(suggestions)
        self.selected_user_ids = {suggestion["user_id"] for suggestion in self.suggestions}
        self.review_message = review_message

        if self.suggestions:
            self.add_item(EvidenceSuggestionSelect(self.suggestions))
        self.add_item(EvidenceAddByTextButton(evidence_message_id, review_message))
        self.add_item(EvidenceAddParticipantsButton(evidence_message_id, review_message))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) == self.author_id or can_review_evidence(interaction):
            return True

        await interaction.response.send_message(
            "Solo quien subio la evidencia o un revisor puede confirmar estos participantes.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(
        label="Confirmar",
        emoji=button_emoji("APPROVED"),
        style=discord.ButtonStyle.success,
    )
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
            record_participants_audit(
                interaction,
                self.evidence_message_id,
                participants,
                "confirmacion_del_autor",
            )
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

    @discord.ui.button(
        label="Ninguno",
        emoji=button_emoji("REJECTED"),
        style=discord.ButtonStyle.secondary,
    )
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
            raw_names = ", ".join(suggestion["raw_names"][:3])
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
        super().__init__(timeout=86400)
        self.evidence_message_id = evidence_message_id
        self.review_message = review_message
        self.suggestions = dedupe_suggestions_by_user(suggestions)
        self.selected_user_ids = {suggestion["user_id"] for suggestion in self.suggestions}
        self.add_item(EvidenceSuggestionSelect(self.suggestions))
        self.add_item(EvidenceAddByTextButton(evidence_message_id, review_message))
        self.add_item(EvidenceAddParticipantsButton(evidence_message_id, review_message))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if can_review_evidence(interaction):
            return True
        await interaction.response.send_message("No tienes permiso.", ephemeral=True)
        return False

    @discord.ui.button(
        label="Agregar",
        emoji=button_emoji("APPROVED"),
        style=discord.ButtonStyle.success,
    )
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

        record_participants_audit(
            interaction,
            self.evidence_message_id,
            participants,
            "sugerencias_del_revisor",
        )
        learn_aliases_from_suggestions(selected)
        await refresh_review_participants(
            self.review_message,
            self.evidence_message_id,
            confirmation_text=f"Sugerencias agregadas por {interaction.user.mention}",
        )
        embed = discord.Embed(
            title=f"{text_emoji('APPROVED')} Participantes agregados",
            description="\n".join(f"<@{user_id}>" for user_id, _ in participants[:25]),
            color=COLOR_SUCCESS,
        )
        await interaction.response.edit_message(embed=embed, view=None)


class EvidenceThreadParticipantView(discord.ui.View):
    def __init__(self, evidence_message_id: str, review_message: discord.Message):
        super().__init__(timeout=86400)
        self.add_item(EvidenceAddByTextButton(evidence_message_id, review_message))
        self.add_item(EvidenceAddParticipantsButton(evidence_message_id, review_message))


class EvidenceReviewView(discord.ui.View):
    def __init__(self, evidence_message_id: str):
        super().__init__(timeout=None)
        self.evidence_message_id = evidence_message_id
        self.add_item(EvidenceAddByTextButton(evidence_message_id))
        self.add_item(EvidenceAddParticipantsButton(evidence_message_id))
        evidence = get_evidence_summary(evidence_message_id)
        if evidence and evidence[2] == "scouteo":
            self.add_item(EvidenceMultiplierButton(evidence_message_id))
        self.add_item(EvidenceApproveButton(evidence_message_id))
        self.add_item(EvidenceRejectButton(evidence_message_id))


class EvidenceAddByTextButton(discord.ui.Button):
    def __init__(self, evidence_message_id: str, review_message: discord.Message | None = None):
        super().__init__(
            label="Texto",
            emoji=button_emoji("EVIDENCE"),
            style=discord.ButtonStyle.secondary,
            custom_id=f"evidence_add_text:{evidence_message_id}"
        )
        self.evidence_message_id = evidence_message_id
        self.review_message = review_message

    async def callback(self, interaction: discord.Interaction):
        if not can_review_evidence(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return

        await interaction.response.send_modal(
            EvidenceParticipantTextModal(self.evidence_message_id, self.review_message or interaction.message)
        )


class EvidenceParticipantTextModal(discord.ui.Modal):
    nombres = discord.ui.TextInput(
        label="Nombres, menciones o IDs",
        placeholder="violeth chino peccato\n+shourout @sherlock 123456789012345678",
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
            record_participants_audit(
                interaction,
                self.evidence_message_id,
                participants,
                "nombres_manuales",
            )
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
    def __init__(self, evidence_message_id: str, review_message: discord.Message | None = None):
        super().__init__(
            label="Personas",
            emoji=button_emoji("SCOUT"),
            style=discord.ButtonStyle.secondary,
            custom_id=f"evidence_add_people:{evidence_message_id}"
        )
        self.evidence_message_id = evidence_message_id
        self.review_message = review_message

    async def callback(self, interaction: discord.Interaction):
        if not can_review_evidence(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"{text_emoji('SCOUT')} Agregar participantes",
            description="Selecciona los usuarios que tambien reciben esta evidencia.",
            color=COLOR_PANEL
        )
        await interaction.response.send_message(
            embed=embed,
            view=EvidenceParticipantSelectView(self.evidence_message_id, self.review_message or interaction.message),
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

        record_participants_audit(
            interaction,
            self.evidence_message_id,
            participants,
            "selector_de_usuarios",
        )
        await refresh_review_participants(self.review_message, self.evidence_message_id)
        await interaction.response.send_message("Personas agregadas.", ephemeral=True)


class EvidenceMultiplierButton(discord.ui.Button):
    def __init__(self, evidence_message_id: str):
        super().__init__(
            label="Multiplicador",
            emoji=button_emoji("MULTIPLIER"),
            style=discord.ButtonStyle.secondary,
            custom_id=f"evidence_multipliers:{evidence_message_id}",
        )
        self.evidence_message_id = str(evidence_message_id)

    async def callback(self, interaction: discord.Interaction):
        if not can_review_evidence(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return

        rows = get_scouteo_review_rows(self.evidence_message_id)
        if not rows:
            await interaction.response.send_message(
                "Este conteo no tiene participantes con multiplicador editable.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=build_scouteo_multiplier_list_embed(rows),
            view=ScouteoMultiplierSelectView(
                self.evidence_message_id,
                interaction.message,
                rows,
            ),
            ephemeral=True,
        )


class ScouteoMultiplierSelectView(discord.ui.View):
    def __init__(self, evidence_message_id: str, review_message: discord.Message, rows: list[dict]):
        super().__init__(timeout=300)
        self.add_item(ScouteoMultiplierSelect(evidence_message_id, review_message, rows))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if can_review_evidence(interaction):
            return True
        await interaction.response.send_message("No tienes permiso.", ephemeral=True)
        return False


class ScouteoMultiplierSelect(discord.ui.Select):
    def __init__(self, evidence_message_id: str, review_message: discord.Message, rows: list[dict]):
        options = [
            discord.SelectOption(
                label=truncate_text(row["username"], 70),
                value=row["user_id"],
                description=(
                    f"x{row['multiplier_hundredths'] / 100:.2f} · "
                    f"{row['units']}u · {row['points']} pts"
                )[:100],
                emoji=button_emoji("MULTIPLIER"),
            )
            for row in rows[:25]
        ]
        super().__init__(
            placeholder="Elige el scout cuyo multiplicador revisar",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.evidence_message_id = str(evidence_message_id)
        self.review_message = review_message

    async def callback(self, interaction: discord.Interaction):
        user_id = self.values[0]
        row = get_scouteo_review_row(self.evidence_message_id, user_id)
        if not row:
            await interaction.response.send_message("Ese participante ya no esta disponible.", ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=build_scouteo_multiplier_detail_embed(row),
            view=ScouteoMultiplierAdjustView(
                self.evidence_message_id,
                user_id,
                self.review_message,
            ),
        )


class ScouteoMultiplierAdjustView(discord.ui.View):
    def __init__(self, evidence_message_id: str, user_id: str, review_message: discord.Message):
        super().__init__(timeout=300)
        self.evidence_message_id = str(evidence_message_id)
        self.user_id = str(user_id)
        self.review_message = review_message

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if can_review_evidence(interaction):
            return True
        await interaction.response.send_message("No tienes permiso.", ephemeral=True)
        return False

    async def apply_delta(self, interaction: discord.Interaction, delta: int):
        row = get_scouteo_review_row(self.evidence_message_id, self.user_id)
        if not row:
            await interaction.response.send_message("Ese participante ya no esta disponible.", ephemeral=True)
            return
        await apply_scouteo_multiplier_change(
            interaction,
            self.evidence_message_id,
            self.user_id,
            row["multiplier_hundredths"] + delta,
            self.review_message,
            self,
        )

    @discord.ui.button(label="-0.05", style=discord.ButtonStyle.danger)
    async def decrease(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.apply_delta(interaction, -5)

    @discord.ui.button(
        label="Exacto",
        emoji=button_emoji("MULTIPLIER"),
        style=discord.ButtonStyle.primary,
    )
    async def exact(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = get_scouteo_review_row(self.evidence_message_id, self.user_id)
        if not row:
            await interaction.response.send_message("Ese participante ya no esta disponible.", ephemeral=True)
            return
        await interaction.response.send_modal(
            ScouteoMultiplierModal(
                self.evidence_message_id,
                self.user_id,
                self.review_message,
                row["multiplier_hundredths"],
            )
        )

    @discord.ui.button(label="+0.05", style=discord.ButtonStyle.success)
    async def increase(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.apply_delta(interaction, 5)

    @discord.ui.button(label="x1", style=discord.ButtonStyle.secondary)
    async def restore(self, interaction: discord.Interaction, button: discord.ui.Button):
        await apply_scouteo_multiplier_change(
            interaction,
            self.evidence_message_id,
            self.user_id,
            100,
            self.review_message,
            self,
        )


class ScouteoMultiplierModal(discord.ui.Modal):
    value = discord.ui.TextInput(
        label="Multiplicador entre 0.70 y 1.00",
        placeholder="Ej: 0.95",
        max_length=4,
    )

    def __init__(
        self,
        evidence_message_id: str,
        user_id: str,
        review_message: discord.Message,
        current: int,
    ):
        super().__init__(title="Ajustar multiplicador")
        self.evidence_message_id = str(evidence_message_id)
        self.user_id = str(user_id)
        self.review_message = review_message
        self.value.default = f"{current / 100:.2f}"

    async def on_submit(self, interaction: discord.Interaction):
        if not can_review_evidence(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return
        try:
            value = float(str(self.value.value).strip().replace(",", "."))
            hundredths = int(round(value * 100))
            if hundredths < 70 or hundredths > 100:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "Escribe un valor entre `0.70` y `1.00`.",
                ephemeral=True,
            )
            return

        previous = get_scouteo_review_row(self.evidence_message_id, self.user_id)
        result = set_scouteo_review_multiplier(
            self.evidence_message_id,
            self.user_id,
            hundredths,
        )
        if not result.get("ok"):
            await interaction.response.send_message(
                scouteo_multiplier_error(result),
                ephemeral=True,
            )
            return
        record_multiplier_audit(
            interaction,
            self.evidence_message_id,
            previous,
            result["row"],
        )
        await refresh_scouteo_review_message(
            self.review_message,
            self.evidence_message_id,
            interaction.user,
            result["row"],
        )
        await interaction.response.send_message(
            embed=build_scouteo_multiplier_detail_embed(
                result["row"],
                f"Actualizado por {interaction.user.mention}",
            ),
            ephemeral=True,
        )


async def apply_scouteo_multiplier_change(
    interaction: discord.Interaction,
    evidence_message_id: str,
    user_id: str,
    value: int,
    review_message: discord.Message,
    view: discord.ui.View,
):
    previous = get_scouteo_review_row(evidence_message_id, user_id)
    result = set_scouteo_review_multiplier(evidence_message_id, user_id, value)
    if not result.get("ok"):
        await interaction.response.send_message(scouteo_multiplier_error(result), ephemeral=True)
        return
    record_multiplier_audit(
        interaction,
        evidence_message_id,
        previous,
        result["row"],
    )
    await refresh_scouteo_review_message(
        review_message,
        evidence_message_id,
        interaction.user,
        result["row"],
    )
    await interaction.response.edit_message(
        embed=build_scouteo_multiplier_detail_embed(
            result["row"],
            f"Actualizado por {interaction.user.mention}",
        ),
        view=view,
    )


def get_scouteo_review_row(evidence_message_id: str, user_id: str):
    return next(
        (
            row
            for row in get_scouteo_review_rows(evidence_message_id)
            if row["user_id"] == str(user_id)
        ),
        None,
    )


def record_multiplier_audit(
    interaction: discord.Interaction,
    evidence_message_id: str,
    previous: dict | None,
    current: dict,
):
    old_multiplier = int((previous or current).get("multiplier_hundredths", 100))
    new_multiplier = int(current.get("multiplier_hundredths", 100))
    record_view_audit(
        interaction,
        "multiplicadores",
        "ajustar",
        target_type="evidencia",
        target_id=evidence_message_id,
        summary=(
            f"{current['username']}: x{old_multiplier / 100:.2f} -> "
            f"x{new_multiplier / 100:.2f} ({current['points']} pts proyectados)."
        ),
        details={
            "scout_id": current["user_id"],
            "scout": current["username"],
            "multiplicador_antes": f"{old_multiplier / 100:.2f}",
            "multiplicador_despues": f"{new_multiplier / 100:.2f}",
            "puntos_antes": (previous or current).get("points", 0),
            "puntos_despues": current.get("points", 0),
        },
    )


def build_scouteo_multiplier_list_embed(rows: list[dict]):
    return discord.Embed(
        title=f"{text_emoji('MULTIPLIER')} Multiplicadores",
        description=(
            f"**{len(rows)} scouts** · elige uno para ajustar.\n\n"
            + "\n".join(format_scouteo_review_line(row) for row in rows[:20])
        )[:4000],
        color=COLOR_WARNING,
    )


def build_scouteo_multiplier_detail_embed(row: dict, status: str | None = None):
    minutes = row["accumulated_minutes"]
    time_text = f"{minutes // 60}h {minutes % 60:02d}m"
    embed = discord.Embed(
        title=f"{text_emoji('MULTIPLIER')} {row['username']}",
        description=(
            f"<@{row['user_id']}> · **x{row['multiplier_hundredths'] / 100:.2f}**\n"
            f"{time_text} · {row['accumulated_maps']} mapas · {row['units']}u\n"
            f"**{row['points']} pts**"
        ),
        color=COLOR_SUCCESS if row["multiplier_hundredths"] == 100 else COLOR_WARNING,
    )
    if status:
        embed.set_footer(text=status)
    return embed


def format_scouteo_review_line(row: dict):
    return (
        f"<@{row['user_id']}> · `x{row['multiplier_hundredths'] / 100:.2f}` · "
        f"`{row['units']}u` → **{row['points']} pts**"
    )


async def refresh_scouteo_review_message(
    review_message: discord.Message,
    evidence_message_id: str,
    actor,
    changed_row: dict,
):
    rows = get_scouteo_review_rows(evidence_message_id)
    if not rows or not review_message.embeds:
        return
    embed = review_message.embeds[0]
    upsert_embed_field(
        embed,
        "Participantes",
        "\n".join(format_scouteo_review_line(row) for row in rows[:20])[:1000],
    )
    upsert_embed_field(
        embed,
        f"{text_emoji('AUDIT')} Ultimo ajuste",
        (
            f"{actor.mention} cambio a <@{changed_row['user_id']}> → "
            f"**x{changed_row['multiplier_hundredths'] / 100:.2f}** "
            f"({changed_row['points']} pts)"
        )[:1000],
    )
    await review_message.edit(embed=embed)


def scouteo_multiplier_error(result: dict):
    return {
        "not_found": "No encontre este conteo de scouteo.",
        "already_reviewed": "La evidencia ya fue revisada; el multiplicador quedo bloqueado.",
        "participant_not_found": "No encontre ese participante en el conteo.",
    }.get(result.get("reason"), "No pude actualizar el multiplicador.")


class EvidenceApproveButton(discord.ui.Button):
    def __init__(self, evidence_message_id: str):
        super().__init__(
            label="Aprobar",
            emoji=button_emoji("APPROVED"),
            style=discord.ButtonStyle.success,
            custom_id=f"evidence_approve:{evidence_message_id}"
        )
        self.evidence_message_id = evidence_message_id

    async def callback(self, interaction: discord.Interaction):
        if not can_review_evidence(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return

        evidence = get_evidence_summary(self.evidence_message_id)
        participants = get_evidence_participants(self.evidence_message_id)
        result = approve_evidence(self.evidence_message_id)
        if not result:
            await interaction.response.send_message("Ya fue revisado.", ephemeral=True)
            return

        record_view_audit(
            interaction,
            "evidencias",
            "aprobar",
            target_type="evidencia",
            target_id=self.evidence_message_id,
            summary=(
                f"Aprobo {evidence[2] if evidence else 'evidencia'} para "
                f"{len(participants) or 1} participante(s)."
            ),
            details={
                "actividad": evidence[2] if evidence else "",
                "participantes": len(participants) or 1,
                "destino_cierre": result[4],
            },
        )
        embed = interaction.message.embeds[0]
        embed.color = COLOR_SUCCESS
        embed.add_field(name="Estado", value=f"Aprobado por {interaction.user.mention}", inline=False)
        await interaction.response.edit_message(embed=embed, view=None)
        await set_review_reaction(interaction, reaction_emoji("APPROVED"))
        await set_source_reaction(interaction, reaction_emoji("APPROVED"))
        await refresh_public_messages(interaction)
        asyncio.create_task(delete_scouteo_dashboard_after_delay(interaction, self.evidence_message_id))


class EvidenceRejectButton(discord.ui.Button):
    def __init__(self, evidence_message_id: str):
        super().__init__(
            label="Rechazar",
            emoji=button_emoji("REJECTED"),
            style=discord.ButtonStyle.danger,
            custom_id=f"evidence_reject:{evidence_message_id}"
        )
        self.evidence_message_id = evidence_message_id

    async def callback(self, interaction: discord.Interaction):
        if not can_review_evidence(interaction):
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return

        evidence = get_evidence_summary(self.evidence_message_id)
        participants = get_evidence_participants(self.evidence_message_id)
        if not reject_evidence(self.evidence_message_id):
            await interaction.response.send_message("Ya fue revisado.", ephemeral=True)
            return

        record_view_audit(
            interaction,
            "evidencias",
            "rechazar",
            target_type="evidencia",
            target_id=self.evidence_message_id,
            summary=(
                f"Rechazo {evidence[2] if evidence else 'evidencia'} para "
                f"{len(participants) or 1} participante(s)."
            ),
            details={
                "actividad": evidence[2] if evidence else "",
                "participantes": len(participants) or 1,
            },
        )
        embed = interaction.message.embeds[0]
        embed.color = COLOR_ERROR
        embed.add_field(name="Estado", value=f"Rechazado por {interaction.user.mention}", inline=False)
        await interaction.response.edit_message(embed=embed, view=None)
        await set_review_reaction(interaction, reaction_emoji("REJECTED"))
        await set_source_reaction(interaction, reaction_emoji("REJECTED"))
        await refresh_public_messages(interaction)
        asyncio.create_task(delete_scouteo_dashboard_after_delay(interaction, self.evidence_message_id))


async def set_review_reaction(interaction: discord.Interaction, emoji):
    try:
        for old_emoji in reaction_variants("APPROVED") + reaction_variants("REJECTED"):
            await interaction.message.clear_reaction(old_emoji)
        await interaction.message.add_reaction(emoji)
    except discord.HTTPException:
        pass


async def set_source_reaction(interaction: discord.Interaction, emoji):
    try:
        source_url = interaction.message.embeds[0].description.split("[Abrir evidencia](")[-1].split(")")[0]
        parts = source_url.rstrip("/").split("/")
        channel_id = int(parts[-2])
        source_message_id = int(parts[-1])
        channel = interaction.client.get_channel(channel_id) or await interaction.client.fetch_channel(channel_id)
        source_message = await channel.fetch_message(source_message_id)
        old_reactions = (
            reaction_variants("APPROVED")
            + reaction_variants("REJECTED")
            + reaction_variants("PENDING")
            + ["\N{OUTBOX TRAY}"]
        )
        for old_emoji in old_reactions:
            await remove_client_reaction(source_message, old_emoji, interaction.client)
        await source_message.add_reaction(emoji)
    except (discord.HTTPException, IndexError, ValueError, AttributeError):
        pass


async def remove_client_reaction(message: discord.Message, emoji, client):
    if not getattr(client, "user", None):
        return
    try:
        await message.remove_reaction(emoji, client.user)
    except discord.HTTPException:
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
    embed = discord.Embed(
        title=f"{text_emoji('AUDIT')} Resolución de Participantes",
        color=COLOR_PANEL,
    )
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
            value=", ".join(f"`{name}`" for name in unresolved[:25])[:1000],
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


async def refresh_public_messages(interaction: discord.Interaction):
    from embeds import build_dashboard_embed, build_info_ranking_embed

    targets = [
        (
            DASHBOARD_CHANNEL_ID,
            ("Ranking Semanal", "Salon del Ranking", "Dashboard Scouts"),
            build_dashboard_embed,
            DashboardView,
        ),
        (
            INFO_RANKING_CHANNEL_ID,
            ("Guía de Evidencias", "Ranking de Evidencias"),
            build_info_ranking_embed,
            InfoRankingView,
        ),
    ]
    client_user = getattr(interaction.client, "user", None)
    if not client_user:
        return

    for channel_id, accepted_titles, embed_builder, view_builder in targets:
        if not channel_id:
            continue
        channel = interaction.client.get_channel(channel_id)
        if not channel:
            try:
                channel = await interaction.client.fetch_channel(channel_id)
            except discord.HTTPException:
                continue
        try:
            async for msg in channel.history(limit=20):
                title = msg.embeds[0].title if msg.embeds else ""
                if msg.author.id == client_user.id and any(
                    accepted in (title or "")
                    for accepted in accepted_titles
                ):
                    await msg.edit(embed=embed_builder(), view=view_builder())
                    break
        except discord.HTTPException:
            continue


async def refresh_dashboard_message(interaction: discord.Interaction):
    """Compatibilidad para llamadas antiguas; ahora refresca ambos paneles."""
    await refresh_public_messages(interaction)

