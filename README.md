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
de tres dashboards con botones, selectores y formularios. Discord solo
registra `/ranking`; las funciones generales y administrativas permanecen
detrás de los botones y conservan sus permisos.

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

| Ruta en `/ranking` | Función | Jerarquia |
|---|---|---|
| **Perfil** | Tu perfil y puntos | General |
| **Operaciones > Scout** | Perfil y ajustes individuales | Officer / Admin |
| **Operaciones > Evidencias > Scouteo** | Conteo auditado de resumen diario | Officer / Admin |
| **Operaciones > Evidencias > Mapeo** | Análisis semanal de logs | Officer / Admin |
| **Operaciones > Puntos** | Ajustes grupales auditados | Officer / Admin |
| **Operaciones > Padrón** | Aliases y alts | Officer / Admin |
| **Operaciones > Publicar** | Guía y ranking público | Officer / Admin |
| **Admin > Prio** | Corte, CSV y sincronización de rol | GM / Lider |
| **Admin > AFK** | Auditoría de inactividad y kicks | GM / Lider |
| **Admin > Exportar** | Ranking actual o cierre en XLSX/CSV | GM / Lider |
| **Admin > Cierre** | Guarda el cierre y limpia el ranking | GM / Lider |
| **Admin > Sistema > Mover conteo** | Corrige el cierre de un conteo | GM / Lider |
| **Admin > Sistema > Checkpoint** | Reinicia el análisis de mapeo | GM / Lider |
| **Admin > Sistema > Paneles** | Actualiza los paneles públicos | GM / Lider |

## Cierres semanales

Antes de cada reset semanal el bot guarda una copia del ranking en `ranking_snapshots` y `ranking_snapshot_rows`. Ese cierre permite usar **Admin > Prio** con la fuente del último cierre para dar o quitar el rol aunque el ranking nuevo ya esté limpio.

Si **Operaciones > Evidencias > Scouteo** se usa después del reset pero el resumen diario trae una fecha de la semana cerrada, la revisión queda apuntando a ese cierre semanal. Al aprobarla suma esos puntos al cierre archivado, no al ranking nuevo.

Para descargar la semana pasada usa **Admin > Exportar > Cierre XLSX/CSV**.
Para corregir esa semana usa **Operaciones > Scout** o **Operaciones > Puntos** con la fuente del último cierre; las restas no bajan una actividad por debajo de 0.

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

Los 24 PNG están en `assets/discord/emojis/` y sus IDs de Discord ya vienen
configurados. El catálogo de `emojis.py` los aplica a dashboards, mensajes,
botones y reacciones sin reutilizar símbolos para conceptos distintos. Si se
usa el bot en otro servidor, cada ID puede reemplazarse desde el entorno.

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
