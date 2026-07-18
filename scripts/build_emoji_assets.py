"""Recorta el atlas aprobado y genera PNG transparentes listos para Discord."""

from pathlib import Path
import sys
from collections import deque

from PIL import Image, ImageDraw


ATLAS_NAMES = [
    "ranking_trofeo",
    "ranking_puntos",
    "ranking_scout",
    "ranking_prio",
    "ranking_evidencia",
    "ranking_pendiente",
    "ranking_aprobado",
    "ranking_rechazado",
    "ranking_auditoria",
    "ranking_multiplicador",
    "ranking_mapeo",
    "ranking_afk",
    "ranking_exportar",
    "ranking_cierre",
    "ranking_config",
]

EXTRA_NAMES = [
    "ranking_actualizar",
    "ranking_kill_scout",
    "ranking_kill_pelea",
    "ranking_limpieza",
    "ranking_scouteo",
    "ranking_personas",
    "ranking_publicar",
    "ranking_paneles",
    "ranking_importar",
]

ALL_NAMES = ATLAS_NAMES + EXTRA_NAMES
REACTION_NAMES = {
    "ranking_pendiente",
    "ranking_aprobado",
    "ranking_rechazado",
}


def chroma_to_alpha(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    pixels = []
    for red, green, blue, _ in rgba.getdata():
        # El atlas usa rosa neon. La distancia conserva los magentas oscuros
        # de la corona y suaviza solo el borde mezclado con el fondo.
        distance = ((red - 255) ** 2 + green ** 2 + (blue - 245) ** 2) ** 0.5
        if distance <= 85:
            alpha = 0
        elif distance >= 145:
            alpha = 255
        else:
            alpha = round((distance - 85) * 255 / 60)
        pixels.append((red, green, blue, alpha))
    rgba.putdata(pixels)
    return rgba


def keep_largest_component(image: Image.Image) -> Image.Image:
    alpha = image.getchannel("A")
    width, height = image.size
    visible = {index for index, value in enumerate(alpha.getdata()) if value > 12}
    components = []
    while visible:
        start = visible.pop()
        component = {start}
        queue = deque([start])
        while queue:
            index = queue.popleft()
            x, y = index % width, index // width
            for ny in range(max(0, y - 1), min(height, y + 2)):
                for nx in range(max(0, x - 1), min(width, x + 2)):
                    neighbor = ny * width + nx
                    if neighbor in visible:
                        visible.remove(neighbor)
                        component.add(neighbor)
                        queue.append(neighbor)
        components.append(component)

    if not components:
        return image
    largest = max(components, key=len)
    output = image.copy()
    cleaned_alpha = [
        value if index in largest else 0
        for index, value in enumerate(alpha.getdata())
    ]
    alpha_band = Image.new("L", image.size)
    alpha_band.putdata(cleaned_alpha)
    output.putalpha(alpha_band)
    return output


def square_icon(
    image: Image.Image,
    size: int = 128,
    keep_only_largest: bool = True,
    edge_margin: int | None = None,
) -> Image.Image:
    if keep_only_largest:
        image = keep_largest_component(image)
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    if not bbox:
        raise ValueError("No se detecto ningun icono en la celda")
    trimmed = image.crop(bbox)
    if edge_margin is not None:
        target = size - (max(0, int(edge_margin)) * 2)
        scale = target / max(trimmed.size)
        resized = trimmed.resize(
            (
                max(1, round(trimmed.width * scale)),
                max(1, round(trimmed.height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )
        output = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        output.alpha_composite(
            resized,
            ((size - resized.width) // 2, (size - resized.height) // 2),
        )
        return output

    padding = max(8, round(max(trimmed.size) * 0.10))
    side = max(trimmed.size) + (padding * 2)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.alpha_composite(
        trimmed,
        ((side - trimmed.width) // 2, (side - trimmed.height) // 2),
    )
    return square.resize((size, size), Image.Resampling.LANCZOS)


def build_preview(icons: list[Image.Image], destination: Path):
    cell = 176
    gap = 12
    columns = 5
    rows = max(1, (len(icons) + columns - 1) // columns)
    width = (cell * columns) + (gap * (columns + 1))
    height = (cell * rows) + (gap * (rows + 1))
    preview = Image.new("RGBA", (width, height), (13, 17, 23, 255))
    draw = ImageDraw.Draw(preview)
    for index, icon in enumerate(icons):
        row, column = divmod(index, columns)
        x = gap + column * (cell + gap)
        y = gap + row * (cell + gap)
        draw.rounded_rectangle(
            (x, y, x + cell, y + cell),
            radius=24,
            fill=(28, 34, 45, 255),
            outline=(56, 65, 82, 255),
            width=2,
        )
        large = icon.resize((144, 144), Image.Resampling.LANCZOS)
        preview.alpha_composite(large, (x + 16, y + 16))
    preview.convert("RGB").save(destination, quality=95)


def repack_reaction_assets(root: Path):
    destination = root / "assets" / "discord" / "emojis"
    for name in REACTION_NAMES:
        path = destination / f"{name}.png"
        icon = Image.open(path).convert("RGBA")
        square_icon(
            icon,
            keep_only_largest=False,
            edge_margin=2,
        ).save(path, optimize=True)

    icons = [
        Image.open(destination / f"{name}.png").convert("RGBA")
        for name in ALL_NAMES
        if (destination / f"{name}.png").exists()
    ]
    build_preview(icons, destination.parent / "ranking-emojis-preview.png")
    print(f"{len(REACTION_NAMES)} emojis de reaccion ampliados en {destination}")


def main():
    root = Path(__file__).resolve().parents[1]
    if len(sys.argv) == 2 and sys.argv[1] == "--repack-reactions":
        repack_reaction_assets(root)
        return

    if len(sys.argv) != 2:
        raise SystemExit(
            "Uso: python scripts/build_emoji_assets.py RUTA_ATLAS.png | --repack-reactions"
        )

    source = Path(sys.argv[1]).resolve()
    destination = root / "assets" / "discord" / "emojis"
    destination.mkdir(parents=True, exist_ok=True)

    atlas = Image.open(source).convert("RGB")
    for index, name in enumerate(ATLAS_NAMES):
        row, column = divmod(index, 5)
        left = round(column * atlas.width / 5)
        right = round((column + 1) * atlas.width / 5)
        top = round(row * atlas.height / 3)
        bottom = round((row + 1) * atlas.height / 3)
        icon = square_icon(
            chroma_to_alpha(atlas.crop((left, top, right, bottom))),
            edge_margin=2 if name in REACTION_NAMES else None,
        )
        icon.save(destination / f"{name}.png", optimize=True)

    icons = [
        Image.open(destination / f"{name}.png").convert("RGBA")
        for name in ALL_NAMES
        if (destination / f"{name}.png").exists()
    ]
    preview = destination.parent / "ranking-emojis-preview.png"
    build_preview(icons, preview)
    print(f"{len(icons)} emojis incluidos en {destination}")
    print(f"Preview: {preview}")


if __name__ == "__main__":
    main()
