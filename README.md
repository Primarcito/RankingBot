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
| `/admin conteo` | Calcula scouteo desde resumen diario | Admin |
| `/admin analizar_mapeo` | Analiza logs de mapeo semanal | Admin |
| `/admin reset_analisis` | Reinicia checkpoint semanal de mapeo | Admin |
| `/admin dashboard_scouts` | Publica o actualiza dashboard de scouts | Admin |
| `/admin info_ranking` | Publica la guia y ranking general | Admin |
| `/admin prio minimo` | Panel semanal para exportar y sincronizar rol prio | Admin |
| `/admin puntos` | Panel para sumar puntos en masa por actividad | Admin |
| `/admin modificar_puntos` | Suma o resta actividades a un scout | Admin |
| `/admin registrar_alt` | Asocia nombres alternos a un scout | Admin |
| `/admin quitar_alt` | Quita un nombre alterno | Admin |
| `/admin ver_alts` | Muestra alts asociados a un scout | Admin |
| `/admin export_ranking` | Exporta el ranking como CSV | Admin |
| `/admin reset_ranking` | Resetea todos los puntos del ranking | Admin |

## Niveles

| Nivel | Puntos | Beneficio |
|---|---|---|
| S | 120+ | Máxima prioridad |
| A | 80–119 | Alta prioridad |
| B | 50–79 | Prioridad media |
| C | 20–49 | Prioridad básica |
| Inactivo | <20 | Sin prioridad |
