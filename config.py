# Mapeo visual de actividades
ACTIVIDADES = {
    "kill_scout":       {"label": "Kill Scout",        "emoji": "🎯"},
    "kill_pelea":       {"label": "Kill Pelea",         "emoji": "⚔️"},
    "limpieza_aspecto": {"label": "Limpieza Aspecto",   "emoji": "🧹"},
    "scouteo":          {"label": "Scouteo",            "emoji": "👁️"},
    "mapeo":            {"label": "Mapeo",              "emoji": "🗺️"},
}

COLOR_PANEL    = 0x2F3136
COLOR_RANKING  = 0xFFD700
COLOR_PERFIL   = 0x5865F2
COLOR_SUCCESS  = 0x57F287
COLOR_ERROR    = 0xED4245
COLOR_WARNING  = 0xFEE75C

APPLICATION_ID = "1505978418867605565"

# IDs configurables
GUILD_ID = 1435778823743340650

ADMIN_ROLE_IDS = set()
REVIEWER_ROLE_IDS = {"1435778823743340652", "1505949443529375845"}

EVIDENCE_CATEGORY_ID = 1505954373275095291
EVIDENCE_CATEGORY_IDS = {1505954373275095291, 15059543732775095291}
EVIDENCE_REVIEW_CHANNEL_ID = 1505983474811801741
DASHBOARD_CHANNEL_ID = 1506054441558605914
LOG_CHANNEL_ID = 0

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
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

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

