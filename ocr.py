import io
import re
import asyncio
import unicodedata
import shutil

from PIL import Image, ImageOps
import pytesseract

from config import OCR_LANG, OCR_MAX_IMAGES, OCR_RULES

TESSERACT_CMD = shutil.which("tesseract")
if TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD


OCR_CORRECTIONS = [
    (r"Kill\s+De[a-z]{3,6}s?\b", "Kill Details"),
    (r"Detal[li1]es", "Detalles"),
    (r"asesin[a4][ft]o", "asesinato"),
    (r"\bL(\s*assist)", r"1\1"),
    (r"\bl(\s*assist)", r"1\1"),
    (r"\b[O0](\s*assist)", r"0\1"),
    (r"\b(\d)assist", r"\1 assist"),
    (r"\bL(\s*ayuda)", r"1\1"),
    (r"\bl(\s*ayuda)", r"1\1"),
    (r"\b[O0](\s*ayuda)", r"0\1"),
    (r"Kill\s+F[ao][mr][mne]e?", "Kill Fame"),
    (r"Fama\s+de\s+asesina[ft]o", "Fama de asesinato"),
    (r"You\s+ki[l1]{2,3}ed", "You killed"),
    (r"Has\s+ma[ft]ado\s+a", "Has matado a"),
    (r"^[&@#]\s*Kill", "Kill", re.MULTILINE),
    (r"jugad\s*[o0]r", "jugador"),
]


def correct_ocr_text(text):
    for correction in OCR_CORRECTIONS:
        if len(correction) == 3:
            pattern, replacement, flags = correction
            text = re.sub(pattern, replacement, text, flags=flags)
        else:
            pattern, replacement = correction
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


async def read_message_ocr(message):
    texts = []
    for attachment in message.attachments[:OCR_MAX_IMAGES]:
        content_type = attachment.content_type or ""
        filename = attachment.filename.lower()
        if not content_type.startswith("image/") and not filename.endswith((".png", ".jpg", ".jpeg", ".webp")):
            continue

        data = await attachment.read()
        image = Image.open(io.BytesIO(data)).convert("RGB")
        image = ImageOps.grayscale(image)
        image = ImageOps.autocontrast(image)
        image = image.point(lambda x: 0 if x < 140 else 255, "1")
        text = await asyncio.to_thread(
            pytesseract.image_to_string,
            image,
            lang=OCR_LANG,
            config="--psm 6",
        )
        if text.strip():
            texts.append(text.strip())

    return "\n".join(texts)


def suggest_activity_from_ocr(text):
    text = correct_ocr_text(text)
    normalized = normalize_text(text)
    matches = {}
    for activity, keywords in OCR_RULES.items():
        hits = [kw for kw in keywords if normalize_text(kw) in normalized]
        if hits:
            matches[activity] = hits

    if not matches:
        return None, [], "Baja"

    if "kill_scout" in matches and "kill_pelea" in matches:
        assists_match = re.search(r"(\d+)\s*(?:assist|ayuda)", normalized)
        if assists_match:
            if int(assists_match.group(1)) > 0:
                matches.pop("kill_scout")
            else:
                matches.pop("kill_pelea")
        else:
            matches.pop("kill_pelea")

    activity = max(matches, key=lambda key: len(matches[key]))
    hits = matches[activity]
    confidence = "Alta" if len(hits) >= 2 else "Media"
    return activity, hits, confidence


def improve_confidence_for_channel(channel_activity, ocr_activity, ocr_hits):
    if not ocr_activity:
        return channel_activity, [], "Baja"
    if ocr_activity == channel_activity:
        confidence = "Alta" if len(ocr_hits) >= 2 else "Media"
        return channel_activity, ocr_hits, confidence
    return channel_activity, ocr_hits, "Media"


def normalize_text(text):
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
