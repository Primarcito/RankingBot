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
        text = await asyncio.to_thread(pytesseract.image_to_string, image, lang=OCR_LANG)
        if text.strip():
            texts.append(text.strip())

    return "\n".join(texts)


def suggest_activity_from_ocr(text):
    normalized = normalize_text(text)
    matches = {}
    for activity, keywords in OCR_RULES.items():
        hits = [kw for kw in keywords if normalize_text(kw) in normalized]
        if hits:
            matches[activity] = hits

    if not matches:
        return None, [], "Baja"

    activity = max(matches, key=lambda key: len(matches[key]))
    hits = matches[activity]
    confidence = "Alta" if len(hits) >= 2 else "Media"
    return activity, hits, confidence


def normalize_text(text):
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
