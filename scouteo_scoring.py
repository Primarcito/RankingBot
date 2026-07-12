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
        # Las unidades son enteras en RankingBot. Floor garantiza que una penalizacion nunca premie.
        item["total"] = (base_total * multiplier) // 100
        calculated.append(item)
    return calculated
