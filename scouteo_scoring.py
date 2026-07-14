import re


MULTIPLIER_RE = re.compile(r"\bx\s*(\d+(?:[.,]\d+)?)\b", re.IGNORECASE)


def parse_multiplier_hundredths(text: str) -> int:
    match = MULTIPLIER_RE.search(text or "")
    if not match:
        return 100

    raw = match.group(1).replace(",", ".")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"Multiplicador invalido: x{raw}") from exc

    hundredths = int(round(value * 100))
    if hundredths < 70 or hundredths > 100 or abs(value * 100 - hundredths) > 1e-9:
        raise ValueError(f"Multiplicador fuera del rango x0.70-x1.00: x{raw}")
    return hundredths


def calculate_scouteo_records(records: list[dict], hours_per_point: int, maps_per_point: int):
    calculated = []
    for record in records:
        item = dict(record)
        hour_points = ((item["hours"] * 60) + item["minutes"]) // (hours_per_point * 60)
        map_points = item["maps"] // maps_per_point
        base_total = hour_points + map_points
        multiplier = int(item.get("multiplier_hundredths", 100))
        if multiplier < 70 or multiplier > 100:
            raise ValueError(f"Multiplicador fuera del rango permitido: {multiplier}")

        item["hour_points"] = hour_points
        item["map_points"] = map_points
        item["base_total"] = base_total
        item["multiplier_hundredths"] = multiplier
        # Las unidades se conservan completas. El multiplicador se aplica a los
        # puntos finales para no quitar una unidad entera por una penalizacion leve.
        item["total"] = base_total
        calculated.append(item)
    return calculated


def calculate_scouteo_points(base_units: int, unit_points: int, multiplier_hundredths: int = 100) -> int:
    numerator = max(0, int(base_units)) * max(0, int(unit_points)) * int(multiplier_hundredths)
    # Redondeo tradicional .5 hacia arriba, sin el redondeo bancario de round().
    return (numerator + 50) // 100


def format_scouteo_summary(record: dict, units: int, unit_points: int, points: int | None = None) -> str:
    hours = int(record.get("hours", 0))
    minutes = int(record.get("minutes", 0))
    if hours and minutes:
        time_text = f"{hours}h{minutes:02d}"
    elif hours:
        time_text = f"{hours}h"
    else:
        time_text = f"{minutes}m"

    multiplier = int(record.get("multiplier_hundredths", 100))
    if multiplier < 100:
        units_text = f"{units}u · x{multiplier / 100:.2f}"
    else:
        units_text = f"{units}u"

    if points is None:
        points = calculate_scouteo_points(units, unit_points, multiplier)

    return (
        f"{points} pts · {units_text} · "
        f"{time_text} · {int(record.get('maps', 0))} mapas"
    )
