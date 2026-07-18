# Emojis de RankingBot

La carpeta `emojis/` contiene 24 PNG transparentes listos para Discord. Todos
ya tienen su ID predeterminado configurado en el bot.

`ranking_aprobado`, `ranking_rechazado` y `ranking_pendiente` usan un margen
especial de 2 px para verse más grandes como reacciones. Se pueden regenerar
sin perder ese encuadre con:

```bash
python scripts/build_emoji_assets.py --repack-reactions
```

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
EMOJI_RANKING_REFRESH_ID=
EMOJI_RANKING_KILL_SCOUT_ID=
EMOJI_RANKING_KILL_FIGHT_ID=
EMOJI_RANKING_CLEANUP_ID=
EMOJI_RANKING_SCOUTING_ID=
EMOJI_RANKING_ROSTER_ID=
EMOJI_RANKING_PUBLISH_ID=
EMOJI_RANKING_PANELS_ID=
EMOJI_RANKING_IMPORT_ID=
```

Si algún ID está vacío, el bot conserva automáticamente su emoji Unicode de
respaldo.

## Previews

- `ranking-emojis-preview.png`: catalogo completo de emojis.
- `ranking-dashboards-preview.png`: entradas separadas `/ranking`, `/conteo` y `/admin`.
- `ranking-review-preview.png`: revision de evidencia y multiplicador individual.
- `ranking-audit-preview.png`: historial de cambios y descarga completa en Markdown.
- `ranking-buttons-preview.png`: actividades y acciones sin emojis ambiguos.
