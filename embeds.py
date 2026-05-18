import discord
from database import get_all_scouts, get_scout, calc_puntos_totales, get_nivel, get_all_config, COLS
from config import ACTIVIDADES, COLOR_RANKING, COLOR_PERFIL, COLOR_PANEL

MEDALS = {0: "🥇", 1: "🥈", 2: "🥉"}


def build_panel_embed() -> discord.Embed:
    config = {a: p for a, p in get_all_config()}
    desc_lines = ["Presiona un botón para registrar tu actividad.\n"]
    for key, meta in ACTIVIDADES.items():
        desc_lines.append(f"{meta['emoji']} **{meta['label']}** — `{config.get(key, 0)} pts`")
    embed = discord.Embed(
        title="⚔️ SISTEMA DE SCOUTS — ALBION ONLINE",
        description="\n".join(desc_lines),
        color=COLOR_PANEL
    )
    embed.set_footer(text="Registro automático · Los botones permanecen activos")
    return embed


def build_ranking_embed(limit: int = 15) -> discord.Embed:
    scouts = get_all_scouts()
    if not scouts:
        return discord.Embed(title="🏆 RANKING DE SCOUTS", description="Sin datos aún.", color=COLOR_RANKING)

    scored = []
    for row in scouts:
        pts = calc_puntos_totales(row)
        nivel, beneficio = get_nivel(pts)
        scored.append((row, pts, nivel, beneficio))
    scored.sort(key=lambda x: x[1], reverse=True)
    scored = scored[:limit]

    embed = discord.Embed(title="🏆 RANKING DE SCOUTS — ALBION ONLINE", color=COLOR_RANKING)
    lines = []
    for i, (row, pts, nivel, beneficio) in enumerate(scored):
        medal = MEDALS.get(i, "🔹")
        username = row[1]
        acts = " · ".join(f"{ACTIVIDADES[col]['emoji']}{row[j+2]}" for j, col in enumerate(COLS))
        lines.append(
            f"{medal} **#{i+1} {username}**  —  `{pts} pts`  `[{nivel}]`\n"
            f"　{acts}\n"
            f"　*{beneficio}*"
        )
    embed.description = "\n\n".join(lines)
    embed.set_footer(text=f"Top {len(scored)} scouts")
    return embed


def build_perfil_embed(user_id: str, display_name: str) -> discord.Embed:
    row = get_scout(user_id)
    if not row:
        return discord.Embed(description="Este usuario no tiene registros aún.", color=COLOR_PERFIL)

    pts = calc_puntos_totales(row)
    nivel, beneficio = get_nivel(pts)

    embed = discord.Embed(
        title=f"🪖 Perfil Scout — {row[1]}",
        color=COLOR_PERFIL
    )
    embed.add_field(name="Total Puntos", value=f"`{pts} pts`", inline=True)
    embed.add_field(name="Nivel", value=f"`{nivel}`", inline=True)
    embed.add_field(name="Beneficio", value=f"`{beneficio}`", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=False)

    for j, col in enumerate(COLS):
        meta = ACTIVIDADES[col]
        embed.add_field(
            name=f"{meta['emoji']} {meta['label']}",
            value=f"`{row[j+2]}`",
            inline=True
        )
    return embed
