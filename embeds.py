import discord

from config import ACTIVIDADES, COLOR_PANEL, COLOR_PERFIL, COLOR_RANKING
from database import (
    COLS,
    calc_puntos_totales,
    get_all_config,
    get_all_scouts,
    get_pending_count,
    get_points_adjustment,
    get_prio_status,
    get_priority_min_points,
    get_scout,
    get_today_evidence_count,
)
from emojis import text_emoji


def _medal(pos: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(pos, f"`#{pos}`")


def _prio_badge(points: int) -> str:
    status = get_prio_status(points)
    return text_emoji("PRIO") if status["qualifies"] else "▫️"


def _bar(value: int, max_value: int, length: int = 8) -> str:
    if max_value <= 0:
        return "░" * length
    filled = max(0, min(round((value / max_value) * length), length))
    return "█" * filled + "░" * (length - filled)


def get_ranked_scouts():
    scouts = get_all_scouts()
    return sorted(
        [(row, calc_puntos_totales(row)) for row in scouts],
        key=lambda item: item[1],
        reverse=True,
    )


def build_dashboard_embed() -> discord.Embed:
    scouts = get_all_scouts()
    points_config = {activity: points for activity, points in get_all_config()}
    ranked = get_ranked_scouts()
    cutoff = get_priority_min_points()

    embed = discord.Embed(
        title=f"{text_emoji('RANKING')} Ranking Semanal · TyrannT",
        description=f"Prio: **{cutoff} pts** · Sin niveles.",
        color=COLOR_PANEL,
    )
    embed.add_field(
        name=f"{text_emoji('AUDIT')} Resumen",
        value=(
            f"{text_emoji('SCOUT')} **{len(scouts)}** scouts · "
            f"{text_emoji('EVIDENCE')} **{get_today_evidence_count()}** hoy · "
            f"{text_emoji('PENDING')} **{get_pending_count()}** pendientes"
        ),
        inline=False,
    )

    activities = [
        f"{meta['emoji']} **{meta['label']}** · `{points_config.get(key, 0)} pts`"
        for key, meta in ACTIVIDADES.items()
    ]
    embed.add_field(
        name=f"{text_emoji('POINTS')} Puntos por unidad",
        value="\n".join(activities),
        inline=False,
    )

    if ranked:
        top_points = ranked[0][1] or 1
        ranking = []
        for pos, (row, points) in enumerate(ranked[:10], start=1):
            ranking.append(
                f"{_medal(pos)} **{row[1]}** · {_prio_badge(points)} "
                f"`{_bar(points, top_points)}` **{points} pts**"
            )
        embed.add_field(
            name=f"{text_emoji('RANKING')} Top 10",
            value="\n".join(ranking),
            inline=False,
        )
    else:
        embed.add_field(
            name=f"{text_emoji('RANKING')} Ranking",
            value="Sin registros.",
            inline=False,
        )
    return embed


def build_ranking_embed(limit: int = 15, page: int = 0, per_page: int | None = None) -> discord.Embed:
    scouts = get_all_scouts()
    ranked_all = get_ranked_scouts()
    if per_page is None:
        ranked = ranked_all[:limit]
        start_pos = 1
        page_text = None
    else:
        page = max(0, page)
        start = page * per_page
        ranked = ranked_all[start:start + per_page]
        start_pos = start + 1
        total_pages = max(1, (len(ranked_all) + per_page - 1) // per_page)
        page_text = f"Pagina {page + 1}/{total_pages}"

    cutoff = get_priority_min_points()
    embed = discord.Embed(
        title=f"{text_emoji('RANKING')} Clasificación Semanal",
        description=None,
        color=COLOR_RANKING,
    )
    if not ranked:
        embed.description = "Aun no hay puntos registrados."
        if page_text:
            embed.set_footer(text=f"{page_text} · Total scouts: {len(scouts)}")
        return embed

    top_points = ranked_all[0][1] or 1
    lines = []
    for pos, (row, points) in enumerate(ranked, start=start_pos):
        status = get_prio_status(points, cutoff)
        prio_text = f"{text_emoji('PRIO')} prio" if status["qualifies"] else f"faltan {status['missing']}"
        lines.append(
            f"{_medal(pos)} **{row[1]}** · **{points} pts** · {prio_text}\n"
            f"`{_bar(points, top_points, 10)}`"
        )
    embed.description = "\n".join(lines)
    footer = f"Corte prio: {cutoff} pts · Total scouts: {len(scouts)}"
    if page_text:
        footer = f"{page_text} · {footer}"
    embed.set_footer(text=footer)
    return embed


def build_ranking_page_count(per_page: int = 10) -> int:
    total = len(get_ranked_scouts())
    return max(1, (total + per_page - 1) // per_page)


def build_info_ranking_embed() -> discord.Embed:
    scouts = get_all_scouts()
    points_config = {activity: points for activity, points in get_all_config()}
    cutoff = get_priority_min_points()

    embed = discord.Embed(
        title=f"{text_emoji('EVIDENCE')} Guía de Evidencias",
        description="Revisión manual antes de sumar puntos.",
        color=COLOR_RANKING,
    )
    embed.add_field(
        name=f"{text_emoji('AUDIT')} Resumen",
        value=(
            f"{text_emoji('SCOUT')} **{len(scouts)}** scouts · "
            f"{text_emoji('EVIDENCE')} **{get_today_evidence_count()}** hoy · "
            f"{text_emoji('PENDING')} **{get_pending_count()}** pendientes · "
            f"{text_emoji('PRIO')} **{cutoff} pts**"
        ),
        inline=False,
    )
    activities = [
        f"{meta['emoji']} **{meta['label']}** · `{points_config.get(key, 0)} pt`"
        for key, meta in ACTIVIDADES.items()
    ]
    embed.add_field(
        name=f"{text_emoji('POINTS')} Puntos por evidencia",
        value="\n".join(activities),
        inline=False,
    )
    embed.add_field(
        name=f"{text_emoji('EVIDENCE')} Como participar",
        value=(
            "**1.** Envía la captura.\n"
            "**2.** Añade participantes: `+Sherlock22 +z1Bell`.\n"
            f"**3.** Espera: {text_emoji('PENDING')} · {text_emoji('APPROVED')} · {text_emoji('REJECTED')}."
        ),
        inline=False,
    )
    return embed


def build_priority_requirement_embed() -> discord.Embed:
    points_config = {activity: points for activity, points in get_all_config()}
    cutoff = get_priority_min_points()
    embed = discord.Embed(
        title=f"{text_emoji('PRIO')} Requisito de Prio",
        description=f"Corte: **{cutoff} pts** · Cumples: prio.",
        color=COLOR_RANKING,
    )
    activity_lines = [
        f"{meta['emoji']} **{meta['label']}** · `{points_config.get(key, 0)} pts`"
        for key, meta in ACTIVIDADES.items()
    ]
    embed.add_field(
        name=f"{text_emoji('POINTS')} Valores actuales",
        value="\n".join(activity_lines),
        inline=False,
    )
    return embed


def build_priority_caps_embed() -> discord.Embed:
    """Alias temporal para botones persistentes publicados antes del cambio."""
    return build_priority_requirement_embed()


def build_perfil_embed(user_id: str, display_name: str) -> discord.Embed:
    scout = get_scout(user_id)
    if not scout:
        return discord.Embed(
            title=f"{text_emoji('SCOUT')} Perfil de Ranking · {display_name}",
            description="Aun no tiene actividades aprobadas en el ranking.",
            color=COLOR_PERFIL,
        )

    points_config = {activity: points for activity, points in get_all_config()}
    total_points = calc_puntos_totales(scout)
    status = get_prio_status(total_points)
    ranked = sorted(get_all_scouts(), key=lambda row: calc_puntos_totales(row), reverse=True)
    position = next((pos for pos, row in enumerate(ranked, start=1) if row[0] == user_id), "?")
    prio_value = (
        f"{text_emoji('APPROVED')} **Califica**\nCorte: {status['minimum']} pts"
        if status["qualifies"]
        else f"{text_emoji('PENDING')} **Aun no**\nFaltan: {status['missing']} pts"
    )

    embed = discord.Embed(
        title=f"{text_emoji('SCOUT')} Perfil de Ranking · {scout[1]}",
        color=COLOR_PERFIL,
    )
    embed.add_field(
        name=f"{text_emoji('POINTS')} Puntos",
        value=f"**{total_points} pts**",
        inline=True,
    )
    embed.add_field(
        name=f"{text_emoji('RANKING')} Posicion",
        value=f"**#{position}** de {len(ranked)}",
        inline=True,
    )
    embed.add_field(
        name=f"{text_emoji('PRIO')} Prio",
        value=prio_value,
        inline=True,
    )

    lines = []
    for index, key in enumerate(COLS, start=2):
        meta = ACTIVIDADES[key]
        count = scout[index]
        lines.append(
            f"{meta['emoji']} {meta['label']}: **{count}x** · `{count * points_config.get(key, 0)} pts`"
        )
    adjustment = get_points_adjustment(user_id)
    if adjustment:
        lines.append(f"{text_emoji('MULTIPLIER')} Ajuste por multiplicadores: **{adjustment:+d} pt**")
    embed.add_field(
        name=f"{text_emoji('EVIDENCE')} Evidencias aprobadas",
        value="\n".join(lines),
        inline=False,
    )
    return embed
