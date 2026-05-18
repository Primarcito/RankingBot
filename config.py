# Mapeo visual de actividades
ACTIVIDADES = {
    "kill_scout":       {"label": "Kill Scout",        "emoji": "🎯"},
    "kill_persona":     {"label": "Kill Persona",       "emoji": "⚔️"},
    "limpieza_aspecto": {"label": "Limpieza Aspecto",   "emoji": "🧹"},
    "scouteo":          {"label": "Scouteo",            "emoji": "👁️"},
    "mapeo":            {"label": "Mapeo",              "emoji": "🗺️"},
    "prio_lider":       {"label": "Prio Líder",         "emoji": "👑"},
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
EVIDENCE_REVIEW_CHANNEL_ID = 1505983474811801741
LOG_CHANNEL_ID = 0

# Permisos
ADMIN_PERMISSION = True

# Evidencias
EVIDENCE_CATEGORY = "evidencias"
EVIDENCE_CHANNELS = {
    "scouts": "scouteo",
    "limpieza": "limpieza_aspecto",
    "peleas": "kill_persona",
    "scouteo": "scouteo",
    "mapeo": "mapeo",
}
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

OCR_ENABLED = True
OCR_MAX_IMAGES = 1
OCR_LANG = "eng"
OCR_RULES = {
    "kill_persona": [
        "has matado a",
        "detalles de asesinato",
        "fama de asesinato",
        "asesino",
    ],
    "limpieza_aspecto": [
        "botin de",
        "botín de",
        "valor est. de mercado",
        "tomar todo",
        "no disponible como botin",
        "no disponible como botín",
    ],
    "mapeo": [
        "wetgrave swale",
        "deathwisp sink",
        "meltwater delta",
        "bono de recoleccion",
        "bono de recolección",
    ],
    "scouteo": [
        "nuestros guardias",
        "han divisado enemigos",
        "entrando en",
    ],
}
