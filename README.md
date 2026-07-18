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

`/ranking` es la entrada principal. Detecta la jerarquia del usuario y abre uno
de tres dashboards con botones, selectores y formularios. Los comandos de
`/admin` se mantienen como respaldo.

Los accesos se agrupan para mantener el panel compacto: **Operaciones** reúne
evidencias, scout, puntos, padrón y publicaciones; **Admin** reúne prio,
valores, exportaciones, AFK, cierres y sistema. Officer/Admin y GM/Lider
también ven **Historial**, con los últimos 6 movimientos y la auditoría
completa en Markdown (`.md`).

| Jerarquia | Dashboard |
|---|---|
| General | **Mi Ranking**: top 3, puntos, posición y estado de prio |
| Officer / Admin | **Evidencias y Puntos**: pendientes y últimas 3 evidencias |
| GM / Lider | **Prio y Cierre**: clasificados, ranking actual, último cierre y próximo reset |

| Comando | Descripción | Jerarquia |
|---|---|---|
| `/ranking` | Abre el dashboard correspondiente | General |
| `/mi_ranking` | Tu perfil y puntos | Todos |
| `/admin perfil usuario` | Perfil y puntos de cualquier scout | Officer / Admin |
| `/admin conteo` | Calcula scouteo desde resumen diario y permite elegir ranking/cierre | Officer / Admin |
| `/admin analizar_mapeo` | Analiza logs de mapeo semanal | Officer / Admin |
| `/admin puntos fuente` | Panel para sumar o restar puntos auditados | Officer / Admin |
| `/admin modificar_puntos fuente` | Ajusta actividades de un scout | Officer / Admin |
| `/admin padron` | Administra aliases y alts | Officer / Admin |
| `/admin info_ranking` | Publica la guia y ranking general | Officer / Admin |
| `/admin mover_conteo_cierre` | Mueve un conteo aprobado al cierre semanal | GM / Lider |
| `/admin reset_analisis` | Reinicia el checkpoint de mapeo | GM / Lider |
| `/admin dashboard_scouts` | Publica o actualiza el dashboard | GM / Lider |
| `/admin prio minimo fuente` | Revisa y sincroniza el rol prio | GM / Lider |
| `/admin afks` | Audita AFKs de dos semanas y permite kickear | GM / Lider |
| `/admin export_ranking fuente formato` | Exporta Excel o CSV | GM / Lider |
| `/admin reset_ranking` | Cierra y resetea el ranking | GM / Lider |

## Cierres semanales

Antes de cada reset semanal el bot guarda una copia del ranking en `ranking_snapshots` y `ranking_snapshot_rows`. Ese cierre permite usar `/admin prio fuente:ultimo_cierre` para dar/quitar el rol prio aunque el ranking nuevo ya este limpio.

Si `/admin conteo` se ejecuta despues del reset pero el resumen diario trae una fecha de la semana cerrada, la revision queda apuntando a ese cierre semanal. Al aprobarla suma esos puntos al cierre archivado, no al ranking nuevo.

Para descargar la semana pasada usa `/admin export_ranking fuente:Ultimo cierre semanal formato:Excel (.xlsx)`.
Para corregir esa semana usa `/admin modificar_puntos fuente:Ultimo cierre semanal` o `/admin puntos fuente:Ultimo cierre semanal`; las restas no bajan una actividad por debajo de 0.

## Conteo acumulado de scouteo

Los resúmenes diarios aprobados acumulan minutos y mapas válidos por scout durante la semana. Se requieren al menos 4 horas acumuladas para habilitar puntos; desde entonces, cada 3 mapas válidos acumulados generan una unidad de scouteo. MapasBot solo reporta un mapa como válido cuando acumula al menos 1 hora de cobertura y conserva entre días los minutos pendientes por mapa. Los mapas sobrantes se conservan y los resúmenes rechazados no modifican el saldo. El saldo actual se limpia junto con el reset semanal del ranking.

Durante la revision, Officer/Admin puede abrir **Multiplicador**, elegir una
persona y ajustar su valor entre `x0.70` y `x1.00`. El cambio recalcula los
puntos finales sin consumir minutos o mapas hasta que la evidencia sea
aprobada.

Los paneles públicos se refrescan al crear, aprobar o rechazar una evidencia,
al cambiar valores y después de cada cierre. La auditoría manual sigue siendo
obligatoria: detectar o contar una evidencia nunca entrega puntos por sí solo.

## Prio

RankingBot no usa niveles S/A/B/C. Solo existe un numero configurable de
puntos para la prio. Los perfiles muestran puntos actuales, corte, si califica
y cuantos puntos faltan.

## Emojis

Los PNG estan en `assets/discord/emojis/` y sus IDs de Discord ya quedaron
registrados en `emojis.py`. RankingBot los aplica a dashboards, mensajes,
botones y reacciones. Las variables de `.env` permiten reemplazar cualquier ID
sin tocar el codigo; el emoji Unicode queda como respaldo.

## Historial

RankingBot guarda en SQLite cada cambio administrativo importante: revisiones
de evidencias, participantes, multiplicadores, puntos, mapeo, prio, padron,
AFKs, publicaciones, exportaciones, configuracion y cierres. Cada evento
conserva fecha UTC, responsable, objetivo, resumen y detalles antes/despues
cuando corresponde.

La descarga se genera desde `/ranking` > **Historial** > **Exportar MD** y
contiene todos los eventos, del mas reciente al mas antiguo. Consultar o
actualizar visualmente un panel no genera ruido; las exportaciones y acciones
que cambian datos si quedan registradas.
