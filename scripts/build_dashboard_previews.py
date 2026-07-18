"""Genera mockups visuales de los paneles de Discord para revision local."""

from pathlib import Path
import textwrap

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
EMOJI_DIR = ROOT / "assets" / "discord" / "emojis"
OUTPUT_DIR = ROOT / "assets" / "discord"
FONT_REGULAR = "C:/Windows/Fonts/segoeui.ttf"
FONT_BOLD = "C:/Windows/Fonts/segoeuib.ttf"


def font(size: int, bold: bool = False):
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size)


def icon(name: str, size: int):
    return Image.open(EMOJI_DIR / f"{name}.png").convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)


def rounded(draw, box, fill, outline=None, radius=20, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def draw_wrapped(draw, text, xy, width_chars, color, size=22, bold=False, spacing=7):
    wrapped = []
    for line in str(text).splitlines() or [""]:
        wrapped.extend(textwrap.wrap(line, width=width_chars) or [""])
    draw.multiline_text(xy, "\n".join(wrapped), font=font(size, bold), fill=color, spacing=spacing)
    return len(wrapped) * (size + spacing)


def draw_button(canvas, draw, x, y, width, label, emoji, style="secondary"):
    colors = {
        "primary": (88, 101, 242),
        "secondary": (78, 80, 88),
        "success": (36, 128, 70),
        "danger": (191, 53, 63),
    }
    rounded(draw, (x, y, x + width, y + 48), colors[style], radius=9)
    canvas.alpha_composite(icon(emoji, 30), (x + 12, y + 9))
    draw.text((x + 50, y + 11), label, font=font(19, True), fill=(255, 255, 255))


def draw_compact_button(canvas, draw, x, y, width, label, emoji, style="secondary"):
    colors = {
        "primary": (88, 101, 242),
        "secondary": (78, 80, 88),
        "success": (36, 128, 70),
        "danger": (191, 53, 63),
    }
    rounded(draw, (x, y, x + width, y + 46), colors[style], radius=9)
    canvas.alpha_composite(icon(emoji, 24), (x + 9, y + 11))
    draw.text((x + 40, y + 12), label, font=font(15, True), fill=(255, 255, 255))


def wrapped_line_count(text, width_chars):
    lines = []
    for line in str(text).splitlines() or [""]:
        lines.extend(textwrap.wrap(line, width=width_chars) or [""])
    return len(lines)


def draw_dashboard_card(canvas, x, title, subtitle, accent, emblem, fields, buttons):
    draw = ImageDraw.Draw(canvas)
    y = 125
    width = 460
    content_y = y + (145 if subtitle else 110)
    cursor = content_y
    for _, value in fields:
        cursor += 31
        cursor += wrapped_line_count(value, 48) * 23
        cursor += 20

    columns = min(3, max(1, len(buttons)))
    button_rows = (len(buttons) + columns - 1) // columns
    button_y = cursor + 8
    button_block_height = button_rows * 46 + max(0, button_rows - 1) * 10
    height = button_y + button_block_height + 26 - y

    rounded(draw, (x, y, x + width, y + height), (43, 45, 49), (70, 72, 78), radius=18, width=2)
    draw.rectangle((x, y, x + 7, y + height), fill=accent)
    canvas.alpha_composite(icon(emblem, 54), (x + 28, y + 25))
    draw.text((x + 95, y + 29), title, font=font(26, True), fill=(245, 246, 247))
    if subtitle:
        draw_wrapped(draw, subtitle, (x + 28, y + 92), 45, (188, 190, 194), 19)

    cursor = content_y
    for field_title, value in fields:
        draw.text((x + 28, cursor), field_title, font=font(18, True), fill=(219, 222, 225))
        cursor += 31
        cursor += draw_wrapped(draw, value, (x + 28, cursor), 48, (188, 190, 194), 18, spacing=5)
        cursor += 20

    gap = 8
    button_width = (404 - gap * (columns - 1)) // columns
    for index, (label, emoji, style) in enumerate(buttons):
        column = index % columns
        row = index // columns
        draw_compact_button(
            canvas,
            draw,
            x + 28 + column * (button_width + gap),
            button_y + row * 56,
            button_width,
            label,
            emoji,
            style,
        )


def build_dashboards():
    canvas = Image.new("RGBA", (1580, 700), (30, 31, 34, 255))
    draw = ImageDraw.Draw(canvas)
    draw.text((48, 34), "RankingBot · dashboards por jerarquia", font=font(36, True), fill=(245, 246, 247))
    draw.text(
        (49, 82),
        "Una sola entrada /ranking; el contenido cambia segun el acceso detectado.",
        font=font(20),
        fill=(166, 168, 173),
    )

    draw_dashboard_card(
        canvas,
        40,
        "Mi Ranking",
        "",
        (45, 156, 184),
        "ranking_scout",
        [
            ("TOP 3", "#1 Violeth · 128 pts\n#2 Sherlock · 114 pts\n#3 ChinoJVS · 97 pts"),
            ("TÚ", "41 pts · #12 · Faltan 9 pts"),
        ],
        [
            ("Perfil", "ranking_scout", "secondary"),
            ("Ranking", "ranking_trofeo", "primary"),
            ("Prio", "ranking_prio", "secondary"),
        ],
    )
    draw_dashboard_card(
        canvas,
        560,
        "Evidencias y Puntos",
        "7 pendientes · Prio: 50 pts",
        (49, 90, 138),
        "ranking_auditoria",
        [
            (
                "ÚLTIMAS",
                "Aprob. · Scouteo · 76 pts · 3p\n"
                "Pend. · Kill Pelea · 24 pts · 4p\n"
                "Rech. · Mapeo · 18 pts · 2p",
            ),
        ],
        [
            ("Perfil", "ranking_scout", "secondary"),
            ("Ranking", "ranking_trofeo", "primary"),
            ("Prio", "ranking_prio", "secondary"),
            ("Operaciones", "ranking_evidencia", "primary"),
            ("Historial", "ranking_auditoria", "secondary"),
        ],
    )
    draw_dashboard_card(
        canvas,
        1080,
        "Prio y Cierre",
        "8 califican · Corte: 50 pts",
        (224, 168, 46),
        "ranking_prio",
        [
            ("ACTUAL", "32 scouts · 1,486 pts"),
            ("CIERRE", "#18 · 31 scouts · 17/07 10:00"),
            ("PRÓXIMO", "En 5 días"),
        ],
        [
            ("Perfil", "ranking_scout", "secondary"),
            ("Ranking", "ranking_trofeo", "primary"),
            ("Prio", "ranking_prio", "secondary"),
            ("Operaciones", "ranking_evidencia", "primary"),
            ("Admin", "ranking_config", "secondary"),
            ("Historial", "ranking_auditoria", "secondary"),
        ],
    )
    canvas.convert("RGB").save(OUTPUT_DIR / "ranking-dashboards-preview.png", quality=95)


def build_review():
    canvas = Image.new("RGBA", (1440, 780), (30, 31, 34, 255))
    draw = ImageDraw.Draw(canvas)
    draw.text((48, 34), "Revision de evidencia · multiplicador por persona", font=font(34, True), fill=(245, 246, 247))
    draw.text(
        (49, 80),
        "El conteo sigue bajo auditoria: nada consume saldos ni entrega puntos antes de Aprobar.",
        font=font(20),
        fill=(166, 168, 173),
    )

    rounded(draw, (50, 135, 835, 725), (43, 45, 49), (70, 72, 78), radius=18, width=2)
    draw.rectangle((50, 135, 57, 725), fill=(229, 139, 42))
    canvas.alpha_composite(icon("ranking_pendiente", 58), (78, 165))
    draw.text((150, 171), "Evidencia pendiente", font=font(28, True), fill=(245, 246, 247))
    draw.text((80, 250), "SCOUTEO · Resumen del Dia → Ranking actual", font=font(20, True), fill=(219, 222, 225))
    draw.text((80, 292), "minimo 4h acumuladas · cada 3 mapas = 1u", font=font(18), fill=(188, 190, 194))
    draw.text((80, 342), "PARTICIPANTES", font=font(17, True), fill=(219, 222, 225))
    participants = [
        ("@Sherlock", "x1.00", "6u", "30 pts"),
        ("@Violeth", "x0.95", "6u", "29 pts"),
        ("@ChinoJVS", "x0.85", "4u", "17 pts"),
    ]
    y = 382
    for name, multiplier, units, points in participants:
        draw.text((82, y), name, font=font(20, True), fill=(88, 101, 242))
        draw.text((300, y), f"{multiplier}  ·  {units}  →  {points}", font=font(20), fill=(219, 222, 225))
        y += 48
    draw.text((80, 530), "ULTIMO AJUSTE", font=font(17, True), fill=(219, 222, 225))
    draw.text((80, 564), "@Officer cambio a @Violeth → x0.95 (29 pts)", font=font(19), fill=(188, 190, 194))

    draw_compact_button(canvas, draw, 80, 645, 110, "Texto", "ranking_evidencia", "secondary")
    draw_compact_button(canvas, draw, 200, 645, 130, "Personas", "ranking_personas", "secondary")
    draw_compact_button(canvas, draw, 340, 645, 155, "Multiplicador", "ranking_multiplicador", "secondary")
    draw_compact_button(canvas, draw, 505, 645, 145, "Aprobar", "ranking_aprobado", "success")
    draw_compact_button(canvas, draw, 660, 645, 145, "Rechazar", "ranking_rechazado", "danger")

    rounded(draw, (875, 135, 1390, 650), (43, 45, 49), (70, 72, 78), radius=18, width=2)
    draw.rectangle((875, 135, 882, 650), fill=(127, 75, 190))
    canvas.alpha_composite(icon("ranking_multiplicador", 70), (905, 166))
    draw.text((995, 172), "Violeth", font=font(28, True), fill=(245, 246, 247))
    draw.text((910, 265), "@Violeth · x0.95", font=font(21), fill=(219, 222, 225))
    draw.text((910, 307), "12h 46m · 18 mapas · 6u", font=font(19), fill=(188, 190, 194))
    draw.text((910, 360), "29 pts", font=font(48, True), fill=(224, 168, 46))
    draw_button(canvas, draw, 910, 455, 130, "-0.05", "ranking_rechazado", "danger")
    draw_button(canvas, draw, 1052, 455, 150, "Exacto", "ranking_multiplicador", "primary")
    draw_button(canvas, draw, 1214, 455, 140, "+0.05", "ranking_aprobado", "success")
    draw_button(canvas, draw, 910, 515, 130, "x1", "ranking_config", "secondary")
    draw.text((910, 585), "Rango: x0.70 – x1.00", font=font(18), fill=(166, 168, 173))

    canvas.convert("RGB").save(OUTPUT_DIR / "ranking-review-preview.png", quality=95)


def build_audit_history():
    canvas = Image.new("RGBA", (1440, 780), (30, 31, 34, 255))
    draw = ImageDraw.Draw(canvas)
    draw.text((48, 34), "RankingBot · historial", font=font(34, True), fill=(245, 246, 247))
    rounded(draw, (50, 135, 875, 720), (43, 45, 49), (70, 72, 78), radius=18, width=2)
    draw.rectangle((50, 135, 57, 720), fill=(49, 90, 138))
    canvas.alpha_composite(icon("ranking_auditoria", 58), (80, 164))
    draw.text((152, 171), "Historial", font=font(28, True), fill=(245, 246, 247))
    draw.text(
        (82, 235),
        "Últimos 6 · historial completo en MD.",
        font=font(19),
        fill=(188, 190, 194),
    )

    events = [
        ("#128 · MULTIPLICADOR ACTUALIZADO", "18:42 UTC · @Violeth", "ChinoJVS: x1.00 → x0.85 (17 pts proyectados)."),
        ("#127 · EVIDENCIA APROBADA", "18:40 UTC · @Violeth", "Scouteo para 3 participantes."),
        ("#126 · PUNTOS SUMADOS EN GRUPO", "18:11 UTC · @Officer", "8 unidades de Kill Pelea a 6 scouts."),
        ("#125 · CORTE DE PRIO ACTUALIZADO", "17:58 UTC · @Johann", "45 → 50 puntos."),
        ("#124 · PADRÓN IMPORTADO", "17:33 UTC · @Officer", "12 aliases nuevos · 2 reasignados."),
        ("#123 · CIERRE MANUAL", "17:02 UTC · @Johann", "Cierre #18 guardado · 31 scouts."),
    ]
    y = 282
    for title, meta, detail in events:
        draw.text((82, y), title, font=font(16, True), fill=(219, 222, 225))
        draw.text((82, y + 27), f"{meta} · {detail}", font=font(15), fill=(188, 190, 194))
        draw.line((82, y + 50, 840, y + 50), fill=(70, 72, 78), width=1)
        y += 59

    draw_button(canvas, draw, 82, 650, 170, "Actualizar", "ranking_actualizar", "secondary")
    draw_button(canvas, draw, 266, 650, 210, "Exportar MD", "ranking_exportar", "primary")

    rounded(draw, (915, 135, 1390, 720), (36, 37, 41), (70, 72, 78), radius=18, width=2)
    canvas.alpha_composite(icon("ranking_exportar", 54), (945, 165))
    draw.text((1015, 173), "Exportación MD", font=font(25, True), fill=(245, 246, 247))
    draw.text((945, 245), "historial_rankingbot_20260718_1842.md", font=font(16, True), fill=(88, 101, 242))
    markdown_lines = [
        "# Historial de RankingBot",
        "",
        "_Generado: 2026-07-18 18:42 UTC_",
        "",
        "## #128 · Ajustes de multiplicador",
        "- Fecha: 2026-07-18 18:42 UTC",
        "- Actor: Violeth (Discord ID)",
        "- Objetivo: evidencia 1527...",
        "- Resumen: ChinoJVS x1.00 → x0.85",
        "- Detalles:",
        "  - Puntos antes: 20",
        "  - Puntos despues: 17",
        "",
        "## #127 · Revisión de evidencias",
        "- Actor: Violeth",
        "- Participantes: 3",
        "",
        "... historial completo ...",
    ]
    y = 285
    mono = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 15)
    for line in markdown_lines:
        draw.text((945, y), line, font=mono, fill=(210, 212, 216))
        y += 23

    canvas.convert("RGB").save(OUTPUT_DIR / "ranking-audit-preview.png", quality=95)


def build_button_semantics():
    canvas = Image.new("RGBA", (1480, 430), (30, 31, 34, 255))
    draw = ImageDraw.Draw(canvas)
    draw.text((48, 34), "RankingBot · botones con identidad propia", font=font(34, True), fill=(245, 246, 247))
    draw.text(
        (49, 80),
        "Cada actividad tiene su símbolo; las repeticiones quedan solo para acciones iguales.",
        font=font(20),
        fill=(166, 168, 173),
    )

    draw.text((50, 145), "ACTIVIDADES", font=font(17, True), fill=(219, 222, 225))
    activity_buttons = [
        ("Kill Scout", "ranking_kill_scout", "secondary", 210),
        ("Kill Pelea", "ranking_kill_pelea", "primary", 210),
        ("Limpieza Aspecto", "ranking_limpieza", "secondary", 250),
        ("Scouteo", "ranking_scouteo", "secondary", 190),
        ("Mapeo", "ranking_mapeo", "secondary", 180),
    ]
    x = 50
    for label, emoji, style, width in activity_buttons:
        draw_button(canvas, draw, x, 180, width, label, emoji, style)
        x += width + 12

    draw.text((50, 275), "ACCIONES", font=font(17, True), fill=(219, 222, 225))
    action_buttons = [
        ("Actualizar", "ranking_actualizar", "secondary", 205),
        ("Personas", "ranking_personas", "secondary", 190),
        ("Publicar", "ranking_publicar", "primary", 185),
        ("Paneles", "ranking_paneles", "secondary", 185),
        ("Importar", "ranking_importar", "success", 185),
    ]
    x = 50
    for label, emoji, style, width in action_buttons:
        draw_button(canvas, draw, x, 310, width, label, emoji, style)
        x += width + 12

    canvas.convert("RGB").save(OUTPUT_DIR / "ranking-buttons-preview.png", quality=95)


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    build_dashboards()
    build_review()
    build_audit_history()
    build_button_semantics()
    print("Previews creados en assets/discord")
