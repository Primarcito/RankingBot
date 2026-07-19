from datetime import datetime, timezone


AUDIT_CATEGORY_LABELS = {
    "multiplicadores": "Ajustes de multiplicador",
    "evidencias": "Revisión de evidencias",
    "participantes": "Participantes",
    "puntos": "Ajustes de puntos",
    "mapeo": "Puntos de mapeo",
    "scouteo": "Puntos de scouteo",
    "prio": "Gestión de prio",
    "padron": "Padrón de scouts",
    "afk": "Revisión AFK",
    "cierres": "Cierres semanales",
    "configuracion": "Configuración de puntos",
    "publicaciones": "Publicaciones del bot",
    "exportaciones": "Exportaciones",
    "sistema": "Sistema",
}

AUDIT_ACTION_LABELS = {
    "ajustar": "Multiplicador actualizado",
    "actualizar": "Multiplicador actualizado",
    "aprobar": "Evidencia aprobada",
    "rechazar": "Evidencia rechazada",
    "crear_revision": "Evidencia enviada a revisión",
    "crear_revision_scouteo": "Scouteo enviado a revisión",
    "preparar_conteo_scouteo": "Conteo preparado",
    "agregar_a_evidencia": "Participantes agregados",
    "cambiar_valor_actividad": "Valor de actividad actualizado",
    "cierre_manual": "Cierre manual ejecutado",
    "reset_manual": "Reset manual ejecutado",
    "reset_automatico": "Reset automático ejecutado",
    "mover_conteo": "Conteo movido al cierre",
    "aprobar_analisis": "Análisis aprobado",
    "rechazar_analisis": "Análisis rechazado",
    "cambiar_tope": "Tope actualizado",
    "cambiar_pesos": "Pesos actualizados",
    "reiniciar_checkpoint": "Checkpoint reiniciado",
    "enviar_a_revision": "Análisis enviado a revisión",
    "cambiar_regla_conteo": "Regla de conteo actualizada",
    "sumar": "Puntos sumados",
    "restar": "Puntos restados",
    "sumar_masivo": "Puntos sumados en grupo",
    "restar_masivo": "Puntos restados en grupo",
    "cambiar_corte": "Corte de prio actualizado",
    "aplicar_roles": "Roles de prio aplicados",
    "publicar_ganadores": "Ganadores de prio publicados",
    "importar_aliases": "Padrón importado",
    "agregar_aliases": "Aliases agregados",
    "quitar_aliases": "Aliases retirados",
    "descartar_candidatos": "Candidatos AFK descartados",
    "kickear_candidatos": "Candidatos AFK expulsados",
    "cambiar_criterio": "Criterio AFK actualizado",
    "actualizar_dashboard": "Dashboard actualizado",
    "actualizar_info_ranking": "Información pública actualizada",
    "actualizar_paneles": "Paneles públicos actualizados",
    "ranking": "Ranking exportado",
    "prio_csv": "Prio exportada",
    "padron_aliases": "Padrón exportado",
    "historial_markdown": "Historial exportado",
}


def audit_category_label(value) -> str:
    key = str(value or "sistema")
    return AUDIT_CATEGORY_LABELS.get(key, key.replace("_", " ").title())


def audit_action_label(value) -> str:
    key = str(value or "evento")
    return AUDIT_ACTION_LABELS.get(key, key.replace("_", " ").title())


def _clean(value) -> str:
    return str(value if value is not None else "").replace("\r", " ").replace("\n", " ").strip()


def _actor_text(event: dict) -> str:
    actor_id = _clean(event.get("actor_id"))
    actor_name = _clean(event.get("actor_name")) or "Sistema"
    return f"{actor_name} (`{actor_id}`)" if actor_id else actor_name


def _target_text(event: dict) -> str:
    target_type = _clean(event.get("target_type"))
    target_id = _clean(event.get("target_id"))
    if target_type and target_id:
        return f"{target_type} `{target_id}`"
    return target_type or (f"`{target_id}`" if target_id else "General")


AUDIT_HIGH_RISK_ACTIONS = {
    "cierre_manual",
    "kickear_candidatos",
    "mover_conteo",
    "quitar_aliases",
    "rechazar",
    "rechazar_analisis",
    "reiniciar_checkpoint",
    "restar",
    "restar_masivo",
    "reset_automatico",
    "reset_manual",
}

AUDIT_MEDIUM_RISK_CATEGORIES = {
    "afk",
    "configuracion",
    "mapeo",
    "multiplicadores",
    "padron",
    "prio",
    "puntos",
    "scouteo",
}

AUDIT_DM_POINT_ACTIONS = {"sumar", "sumar_masivo"}


def is_audit_dm_relevant(event: dict) -> bool:
    category = str(event.get("category") or "").casefold()
    action = str(event.get("action") or "").casefold()
    return (
        category == "multiplicadores"
        or (category == "puntos" and action in AUDIT_DM_POINT_ACTIONS)
    )


def audit_event_risk(event: dict) -> str:
    action = str(event.get("action") or "").casefold()
    category = str(event.get("category") or "").casefold()
    if action in AUDIT_HIGH_RISK_ACTIONS:
        return "high"
    if category in AUDIT_MEDIUM_RISK_CATEGORIES:
        return "medium"
    return "low"


def format_audit_dm_line(event: dict) -> str:
    risk_icon = {"low": "🟢", "medium": "🟠", "high": "🔴"}[audit_event_risk(event)]
    actor_id = _clean(event.get("actor_id"))
    actor = _clean(event.get("actor_name")) or "Sistema"
    if actor_id:
        actor = f"{actor} <@{actor_id}>"
    action = audit_action_label(event.get("action"))
    target = _target_text(event)
    summary = _clean(event.get("summary"))
    parts = [risk_icon, action, actor, target]
    if summary:
        parts.append(summary)
    return " · ".join(parts)[:1000]


def build_audit_markdown(events: list[dict], generated_at=None) -> str:
    generated_at = generated_at or datetime.now(timezone.utc)
    lines = [
        "# Historial de RankingBot",
        "",
        "## Registro completo de cambios",
        "",
        f"_Generado: {generated_at.strftime('%Y-%m-%d %H:%M UTC')} · Entradas: {len(events)}_",
        "",
        "Los cambios aparecen del más reciente al más antiguo.",
        "",
    ]
    if not events:
        lines.append("No hay movimientos registrados.")
        return "\n".join(lines) + "\n"

    for event in events:
        timestamp = _clean(event.get("created_at")).replace("T", " ")
        category = audit_category_label(event.get("category"))
        action = audit_action_label(event.get("action"))
        lines.extend([
            f"## #{event.get('id')} · {category} · {action}",
            "",
            f"- **Fecha:** `{timestamp}`",
            f"- **Actor:** {_actor_text(event)}",
            f"- **Objetivo:** {_target_text(event)}",
        ])
        summary = _clean(event.get("summary"))
        if summary:
            lines.append(f"- **Resumen:** {summary}")
        details = event.get("details") or {}
        if details:
            lines.append("- **Detalles:**")
            for key, value in sorted(details.items()):
                lines.append(f"  - **{_clean(key).replace('_', ' ').title()}:** `{_clean(value)}`")
        lines.append("")

    return "\n".join(lines)
