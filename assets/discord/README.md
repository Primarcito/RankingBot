# Emojis de RankingBot

La carpeta `emojis/` contiene los 15 PNG transparentes subidos a Discord. Sus
nombres e IDs coinciden con el catálogo de `emojis.py`.

Los IDs ya estan incluidos como valores predeterminados. Estas variables son
opcionales y sirven para reemplazarlos en otro servidor:

```env
EMOJI_RANKING_RANKING_ID=
EMOJI_RANKING_POINTS_ID=
EMOJI_RANKING_SCOUT_ID=
EMOJI_RANKING_PRIO_ID=
EMOJI_RANKING_EVIDENCE_ID=
EMOJI_RANKING_PENDING_ID=
EMOJI_RANKING_APPROVED_ID=
EMOJI_RANKING_REJECTED_ID=
EMOJI_RANKING_AUDIT_ID=
EMOJI_RANKING_MULTIPLIER_ID=
EMOJI_RANKING_MAP_ID=
EMOJI_RANKING_AFK_ID=
EMOJI_RANKING_EXPORT_ID=
EMOJI_RANKING_CALENDAR_ID=
EMOJI_RANKING_SETTINGS_ID=
```

Si algún ID está vacío, el bot conserva automáticamente su emoji Unicode de
respaldo.

## Previews

- `ranking-emojis-preview.png`: catalogo completo de emojis.
- `ranking-dashboards-preview.png`: dashboards General, Officer/Admin y GM/Lider.
- `ranking-review-preview.png`: revision de evidencia y multiplicador individual.
- `ranking-audit-preview.png`: historial de cambios y descarga completa en Markdown.
