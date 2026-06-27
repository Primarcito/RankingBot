# 🗡️ Scout Ranking Bot — Albion Online

Bot de Discord para registro y ranking de scouts.

## Setup

```bash
pip install -r requirements.txt
```

1. Copia `.env.example` → `.env`
2. Pega tu token en `.env`
3. En el Portal de Discord habilita:
   - **Server Members Intent**
   - **Message Content Intent** (opcional)
   - Scopes: `bot` + `applications.commands`
   - Permisos: `Send Messages`, `Embed Links`, `Attach Files`

```bash
python main.py
```

## Verificacion segura

Antes de desplegar cambios del bot:

```bash
python scripts/check_bot.py
```

Este chequeo solo compila codigo y busca nombres faltantes en `main.py`; no abre, modifica ni borra la base de datos.

## Comandos

| Comando | Descripción | Permisos |
|---|---|---|
| `/mi_ranking` | Tu perfil y puntos | Todos |
| `/admin perfil usuario` | Perfil y puntos de cualquier scout | Admin |
| `/admin conteo` | Calcula scouteo desde resumen diario y permite elegir ranking/cierre | Admin |
| `/admin mover_conteo_cierre` | Mueve un conteo aprobado del ranking actual al cierre semanal | Admin |
| `/admin analizar_mapeo` | Analiza logs de mapeo semanal | Admin |
| `/admin reset_analisis` | Reinicia checkpoint semanal de mapeo | Admin |
| `/admin dashboard_scouts` | Publica o actualiza dashboard de scouts | Admin |
| `/admin info_ranking` | Publica la guia y ranking general | Admin |
| `/admin prio minimo fuente` | Panel semanal para exportar y sincronizar rol prio | Admin |
| `/admin afks max_puntos limite` | Revisa AFKs de todo el Discord con 0 puntos en ranking actual y ultimo cierre; el kick requiere rol GM | Admin/GM |
| `/admin puntos fuente` | Panel para sumar o restar puntos en masa por actividad, en ranking actual o ultimo cierre | Admin |
| `/admin modificar_puntos fuente` | Suma o resta actividades a un scout, en ranking actual o ultimo cierre | Admin |
| `/admin registrar_alt` | Asocia nombres alternos a un scout | Admin |
| `/admin quitar_alt` | Quita un nombre alterno | Admin |
| `/admin ver_alts` | Muestra alts asociados a un scout | Admin |
| `/admin export_ranking fuente formato` | Exporta el ranking actual o el ultimo cierre semanal como Excel o CSV | Admin |
| `/admin reset_ranking` | Resetea todos los puntos del ranking | Admin |

## Cierres semanales

Antes de cada reset semanal el bot guarda una copia del ranking en `ranking_snapshots` y `ranking_snapshot_rows`. Ese cierre permite usar `/admin prio fuente:ultimo_cierre` para dar/quitar el rol prio aunque el ranking nuevo ya este limpio.

Si `/admin conteo` se ejecuta despues del reset pero el resumen diario trae una fecha de la semana cerrada, la revision queda apuntando a ese cierre semanal. Al aprobarla suma esos puntos al cierre archivado, no al ranking nuevo.

Para descargar la semana pasada usa `/admin export_ranking fuente:Ultimo cierre semanal formato:Excel (.xlsx)`.
Para corregir esa semana usa `/admin modificar_puntos fuente:Ultimo cierre semanal` o `/admin puntos fuente:Ultimo cierre semanal`; las restas no bajan una actividad por debajo de 0.

## Niveles

| Nivel | Puntos | Beneficio |
|---|---|---|
| S | 120+ | Máxima prioridad |
| A | 80–119 | Alta prioridad |
| B | 50–79 | Prioridad media |
| C | 20–49 | Prioridad básica |
| Inactivo | <20 | Sin prioridad |
