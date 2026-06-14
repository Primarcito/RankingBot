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

## Comandos

| Comando | Descripción | Permisos |
|---|---|---|
| `/panel_scouts` | Panel con botones de actividad | Todos |
| `/ranking_scouts` | Ranking top 15 | Todos |
| `/perfil_scout` | Tu perfil | Todos |
| `/perfil_scout_usuario @user` | Perfil de otro usuario | Todos |
| `/prio minimo` | Panel semanal para exportar y sincronizar el rol prio | Admin |
| `/set_puntos actividad cantidad` | Cambia el valor de una actividad | Admin |
| `/sumar_scout @user actividad cantidad` | Suma manualmente | Admin |
| `/restar_scout @user actividad cantidad` | Resta manualmente | Admin |
| `/reset_scouts` | Resetea todos los conteos | Admin |
| `/exportar_scouts` | Exporta CSV | Admin |

## Niveles

| Nivel | Puntos | Beneficio |
|---|---|---|
| S | 120+ | Máxima prioridad |
| A | 80–119 | Alta prioridad |
| B | 50–79 | Prioridad media |
| C | 20–49 | Prioridad básica |
| Inactivo | <20 | Sin prioridad |
