import discord
from database import (
    COLS,
    calc_puntos_totales,
    get_all_config,
    get_all_scouts,
    get_nivel,
    get_pending_count,
    get_scout,
    get_today_evidence_count,
)
from config import ACTIVIDADES, COLOR_PANEL, COLOR_PERFIL, COLOR_RANKING


def _medal(pos: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(pos, f"`#{pos}`")


def _nivel_badge(nivel: str) -> str:
    return {
        "S": "🟣",
        "A": "🟡",
        "B": "⚪",
        "C": "🟠",
        "Inactivo": "⬛",
    }.get(nivel, "⬛")


def _bar(value: int, max_value: int, length: int = 8) -> str:
    if max_value <= 0:
        return "░" * length
    filled = max(0, min(round((value / max_value) * length), length))
    return "█" * filled + "░" * (length - filled)


def build_dashboard_embed() -> discord.Embed:
    scouts = get_all_scouts()
    points_config = {activity: points for activity, points in get_all_config()}
    ranked = sorted(
        [(row, calc_puntos_totales(row)) for row in scouts],
        key=lambda item: item[1],
        reverse=True,
    )

    embed = discord.Embed(title="⚔️ Dashboard Scouts - TyrannT", color=COLOR_PANEL)
    embed.add_field(
        name="📊 Resumen",
        value=(
            f"👥 Scouts registrados: **{len(scouts)}**\n"
            f"📋 Evidencias hoy: **{get_today_evidence_count()}**\n"
            f"⏳ Pendientes revision: **{get_pending_count()}**"
        ),
        inline=False,
    )

    activities = [
        f"{meta['emoji']} **{meta['label']}** - `{points_config.get(key, 0)} pts`"
        for key, meta in ACTIVIDADES.items()
    ]
    embed.add_field(name="🎯 Actividades", value="\n".join(activities), inline=False)

    if ranked:
        top_points = ranked[0][1] or 1
        ranking = []
        for pos, (row, points) in enumerate(ranked[:10], start=1):
            nivel, _ = get_nivel(points)
            ranking.append(
                f"{_medal(pos)} **{row[1]}**\n"
                f"  {_nivel_badge(nivel)} {nivel} `{_bar(points, top_points)}` **{points} pts**"
            )
        embed.add_field(name="🏆 Ranking Top 10", value="\n".join(ranking), inline=False)
    else:
        embed.add_field(name="🏆 Ranking", value="Sin datos aun.", inline=False)

    embed.set_footer(text="Usa los botones de abajo - respuestas privadas")
    return embed


def build_ranking_embed(limit: int = 15) -> discord.Embed:
    scouts = get_all_scouts()
    ranked = sorted(
        [(row, calc_puntos_totales(row)) for row in scouts],
        key=lambda item: item[1],
        reverse=True,
    )[:limit]

    embed = discord.Embed(title="🏆 Ranking Scouts - TyrannT", color=COLOR_RANKING)
    if not ranked:
        embed.description = "Sin datos aun."
        return embed

    top_points = ranked[0][1] or 1
    lines = []
    for pos, (row, points) in enumerate(ranked, start=1):
        nivel, _ = get_nivel(points)
        lines.append(
            f"{_medal(pos)} **{row[1]}**\n"
            f"  {_nivel_badge(nivel)} {nivel} `{_bar(points, top_points)}` **{points} pts**"
        )
    embed.description = "\n".join(lines)
    embed.set_footer(text=f"Total scouts: {len(scouts)}")
    return embed


def build_perfil_embed(user_id: str, display_name: str) -> discord.Embed:
    scout = get_scout(user_id)
    if not scout:
        return discord.Embed(
            description=f"**{display_name}** aun no tiene actividades registradas.",
            color=COLOR_PERFIL,
        )

    points_config = {activity: points for activity, points in get_all_config()}
    total_points = calc_puntos_totales(scout)
    nivel, beneficio = get_nivel(total_points)
    ranked = sorted(get_all_scouts(), key=lambda row: calc_puntos_totales(row), reverse=True)
    position = next((pos for pos, row in enumerate(ranked, start=1) if row[0] == user_id), "?")

    embed = discord.Embed(title=f"👤 {scout[1]}", color=COLOR_PERFIL)
    embed.add_field(name="🏅 Nivel", value=f"{_nivel_badge(nivel)} **{nivel}**\n{beneficio}", inline=True)
    embed.add_field(name="🏆 Ranking", value=f"**#{position}** de {len(ranked)}", inline=True)
    embed.add_field(name="⭐ Puntos", value=f"**{total_points} pts**", inline=True)

    lines = []
    for index, key in enumerate(COLS, start=2):
        meta = ACTIVIDADES[key]
        count = scout[index]
        lines.append(f"{meta['emoji']} {meta['label']}: **{count}x** -> `{count * points_config.get(key, 0)} pts`")
    embed.add_field(name="📋 Actividades", value="\n".join(lines), inline=False)
    return embed
