import os

from emojis import activity_text_emoji


# Mapeo visual de actividades. El catalogo central permite activar los emojis
# personalizados con variables de entorno sin tocar los paneles.
ACTIVIDADES = {
    "kill_scout":       {"label": "Kill Scout",       "emoji": activity_text_emoji("kill_scout")},
    "kill_pelea":       {"label": "Kill Pelea",       "emoji": activity_text_emoji("kill_pelea")},
    "limpieza_aspecto": {"label": "Limpieza Aspecto", "emoji": activity_text_emoji("limpieza_aspecto")},
    "scouteo":          {"label": "Scouteo",          "emoji": activity_text_emoji("scouteo")},
    "mapeo":            {"label": "Mapeo",            "emoji": activity_text_emoji("mapeo")},
}

COLOR_PANEL    = 0x315A8A  # azul heráldico
COLOR_RANKING  = 0xE0A82E  # oro
COLOR_PERFIL   = 0x2D9CB8  # turquesa scout
COLOR_SUCCESS  = 0x3FAE62  # verde escudo
COLOR_ERROR    = 0xD94A4A  # rojo lacre
COLOR_WARNING  = 0xE58B2A  # ámbar

APPLICATION_ID = "1505978418867605565"

# IDs configurables
GUILD_ID = 1435778823743340650

def _role_ids(name: str, defaults: set[str]):
    raw = os.getenv(name, "").strip()
    if not raw:
        return set(defaults)
    return {item.strip() for item in raw.split(",") if item.strip().isdigit()}


ADMIN_ROLE_IDS = _role_ids("RANKING_ADMIN_ROLE_IDS", {"1435778823743340652"})
REVIEWER_ROLE_IDS = _role_ids(
    "RANKING_OFFICER_ROLE_IDS",
    {"1435778823743340652", "1505949443529375845"},
)
GM_ROLE_IDS = _role_ids("RANKING_GM_LEADER_ROLE_IDS", {"1435778823743340652"})
# Jerarquia funcional del bot:
# 0 General · 1 Officer/Admin · 2 GM/Lider.
# Los conjuntos conservan los IDs existentes y se pueden ampliar en despliegue.
OFFICER_ADMIN_ROLE_IDS = ADMIN_ROLE_IDS | REVIEWER_ROLE_IDS
GM_LEADER_ROLE_IDS = GM_ROLE_IDS
PRIORITY_PROTECTED_ROLE_IDS = ADMIN_ROLE_IDS | REVIEWER_ROLE_IDS | GM_ROLE_IDS

EVIDENCE_CATEGORY_ID = 1505954373275095291
EVIDENCE_CATEGORY_IDS = {1505954373275095291, 15059543732775095291}
EVIDENCE_REVIEW_CHANNEL_ID = 1505983474811801741
DASHBOARD_CHANNEL_ID = 1506054441558605914
INFO_RANKING_CHANNEL_ID = 1506143923490258964
WEEKLY_EXPORT_CHANNEL_ID = 1435778824775274581
LOG_CHANNEL_ID = 0

# Reset semanal del ranking. 0 = lunes, 6 = domingo.
AUTO_RESET_ENABLED = True
AUTO_RESET_WEEKDAY_UTC = 0
AUTO_RESET_HOUR_UTC = 10
AUTO_RESET_MINUTE_UTC = 0

PRIORITY_ROLE_ID = 1506387790265581588
DEFAULT_PRIORITY_MIN_POINTS = 50

EVIDENCE_CHANNEL_IDS = {
    1505954534852268214: "kill_scout",
    1505955261930541166: "limpieza_aspecto",
    1505954779464208425: "kill_pelea",
    1505984531063377970: "scouteo",
    1505954990756204755: "mapeo",
}

# Permisos
ADMIN_PERMISSION = True

# Evidencias
EVIDENCE_CATEGORY = "evidencias"
EVIDENCE_CHANNELS = {
    "scouts": "scouteo",
    "limpieza": "limpieza_aspecto",
    "peleas": "kill_pelea",
    "scouteo": "scouteo",
    "mapeo": "mapeo",
}
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")

OCR_ENABLED = True
OCR_MAX_IMAGES = 1
OCR_LANG = "spa+eng"
OCR_RULES = {
    "kill_scout": [
        "kill details",
        "detalles de asesinato",
        "kill fame",
        "fama de asesinato",
        "you killed",
        "has matado a",
        "kill player",
        "matar a jugador",
        "suspicious",
        "reputation",
    ],
    "kill_pelea": [
        "kill details",
        "detalles de asesinato",
        "kill fame",
        "fama de asesinato",
        "you killed",
        "has matado a",
        "kill player",
        "matar a jugador",
        "assist",
        "ayuda",
    ],
    "limpieza_aspecto": [
        "botin de",
        "botín de",
        "valor est. de mercado",
        "valor est de mercado",
        "tomar todo",
        "no disponible como botin",
        "no disponible como botín",
        "heretic",
        "heretico",
        "undead",
        "muerto viviente",
        "keeper",
        "morgana",
        "dungeon",
        "aspecto de",
        "aspect of",
    ],
    "mapeo": [
        "wetgrave swale",
        "deathwisp sink",
        "meltwater delta",
        "bono de recoleccion",
        "bono de recolección",
        "roads of avalon",
        "caminos de avalon",
        "map fragment",
        "fragmento de mapa",
    ],
    "scouteo": [
        "nuestros guardias",
        "han divisado enemigos",
        "entrando en",
        "enemy spotted",
        "hostile",
    ],
}

