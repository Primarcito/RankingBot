"""Catalogo visual central de RankingBot.

Los IDs se configuran despues de subir los PNG a Discord:
EMOJI_RANKING_<CLAVE>_ID=123456789012345678

Mientras un ID no exista, el bot usa el emoji Unicode de respaldo. Esto permite
probar todos los paneles antes de subir los emojis personalizados.
"""

from dataclasses import dataclass
import os

import discord


@dataclass(frozen=True)
class EmojiDefinition:
    name: str
    fallback: str
    default_id: int


DEFINITIONS = {
    "RANKING": EmojiDefinition("ranking_trofeo", "🏆", 1527938742520774696),
    "POINTS": EmojiDefinition("ranking_puntos", "🪙", 1527938737382887434),
    "SCOUT": EmojiDefinition("ranking_scout", "🏹", 1527938740675416114),
    "PRIO": EmojiDefinition("ranking_prio", "👑", 1527938735369486386),
    "EVIDENCE": EmojiDefinition("ranking_evidencia", "📜", 1527938724246327336),
    "PENDING": EmojiDefinition("ranking_pendiente", "⏳", 1527938733654016040),
    "APPROVED": EmojiDefinition("ranking_aprobado", "✅", 1527938715853262908),
    "REJECTED": EmojiDefinition("ranking_rechazado", "❌", 1527938739110940692),
    "AUDIT": EmojiDefinition("ranking_auditoria", "👁️", 1527938718340747354),
    "MULTIPLIER": EmojiDefinition("ranking_multiplicador", "✖️", 1527938731892412496),
    "MAP": EmojiDefinition("ranking_mapeo", "🗺️", 1527938730000908348),
    "AFK": EmojiDefinition("ranking_afk", "💤", 1527938714125205525),
    "EXPORT": EmojiDefinition("ranking_exportar", "📦", 1527938726116855890),
    "CALENDAR": EmojiDefinition("ranking_cierre", "📅", 1527938720312066139),
    "SETTINGS": EmojiDefinition("ranking_config", "⚙️", 1527938722275004516),
    "REFRESH": EmojiDefinition("ranking_actualizar", "🔄", 1527966050287616040),
    "KILL_SCOUT": EmojiDefinition("ranking_kill_scout", "🎯", 1527966066154668113),
    "KILL_FIGHT": EmojiDefinition("ranking_kill_pelea", "⚔️", 1527966064171024424),
    "CLEANUP": EmojiDefinition("ranking_limpieza", "🧹", 1527966070353301585),
    "SCOUTING": EmojiDefinition("ranking_scouteo", "🔭", 1527966086925123685),
    "ROSTER": EmojiDefinition("ranking_personas", "👥", 1527966077567500358),
    "PUBLISH": EmojiDefinition("ranking_publicar", "📣", 1527966080545460264),
    "PANELS": EmojiDefinition("ranking_paneles", "🗂️", 1527966074581287002),
    "IMPORT": EmojiDefinition("ranking_importar", "📥", 1527966062157631579),
}

ACTIVITY_EMOJI_KEYS = {
    "kill_scout": "KILL_SCOUT",
    "kill_pelea": "KILL_FIGHT",
    "limpieza_aspecto": "CLEANUP",
    "scouteo": "SCOUTING",
    "mapeo": "MAP",
}


def emoji_id(key: str) -> int | None:
    raw = os.getenv(f"EMOJI_RANKING_{key.upper()}_ID", "").strip()
    return int(raw) if raw.isdigit() else DEFINITIONS[key.upper()].default_id


def text_emoji(key: str) -> str:
    definition = DEFINITIONS[key.upper()]
    custom_id = emoji_id(key)
    if custom_id:
        return f"<:{definition.name}:{custom_id}>"
    return definition.fallback


def button_emoji(key: str):
    definition = DEFINITIONS[key.upper()]
    custom_id = emoji_id(key)
    if custom_id:
        return discord.PartialEmoji(name=definition.name, id=custom_id)
    return definition.fallback


def reaction_emoji(key: str):
    return button_emoji(key)


def reaction_variants(key: str):
    definition = DEFINITIONS[key.upper()]
    current = reaction_emoji(key)
    return [current] if current == definition.fallback else [current, definition.fallback]


def activity_text_emoji(activity: str) -> str:
    return text_emoji(ACTIVITY_EMOJI_KEYS.get(activity, "EVIDENCE"))


def activity_button_emoji(activity: str):
    return button_emoji(ACTIVITY_EMOJI_KEYS.get(activity, "EVIDENCE"))
