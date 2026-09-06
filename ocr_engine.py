"""
ocr_engine.py
-------------
Thin wrapper around pytesseract for:
  1. Full-text extraction from a label/package image (for declaration
     detection via regex in rules_engine).
  2. Word-level bounding boxes, used to estimate the printed height of the
     net-quantity string in millimetres (for the font-size / Second Schedule
     check), given the real-world width of the Principal Display Panel.

Swap-in points for a production system:
  - Replace pytesseract with a fine-tuned scene-text model (e.g. PaddleOCR /
    a Textract-style layout model) for skewed, curved or low-contrast labels.
  - Add perspective correction / deskew before OCR (OpenCV) for photos taken
    at an angle.
"""

from dataclasses import dataclass

import pytesseract
from PIL import Image, ImageOps


@dataclass
class OCRWord:
    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float


def preprocess(image: Image.Image) -> Image.Image:
    """Light preprocessing: EXIF-orient, greyscale, upscale small images.
    Kept deliberately simple for the prototype; a production pipeline would
    add adaptive thresholding, deskew and glare removal."""
    image = ImageOps.exif_transpose(image)
    image = image.convert("L")
    if max(image.size) < 1200:
        scale = 1200 / max(image.size)
        image = image.resize((int(image.width * scale), int(image.height * scale)))
    return image


def extract_text(image: Image.Image) -> str:
    img = preprocess(image)
    return pytesseract.image_to_string(img)


def extract_words(image: Image.Image, min_conf: float = 40.0):
    img = preprocess(image)
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    words = []
    for i in range(len(data["text"])):
        txt = data["text"][i].strip()
        conf = float(data["conf"][i]) if data["conf"][i] not in ("-1", "") else -1
        if txt and conf >= min_conf:
            words.append(OCRWord(
                text=txt,
                left=data["left"][i], top=data["top"][i],
                width=data["width"][i], height=data["height"][i],
                conf=conf,
            ))
    return words, img.size  # (words, (image_width_px, image_height_px))


def net_quantity_glyph_heights_mm(words, image_px_width, pdp_width_cm):
    """Estimate the printed height (mm) of any OCR word that looks like the
    net-quantity value (a number followed/preceded by a unit token), using a
    px->mm scale derived from the user-supplied real-world PDP width.

    This assumes the photo is roughly front-on and the PDP fills the frame
    width — both are stated to the user in the UI as required conditions for
    a reliable font-size measurement; otherwise the check is downgraded to
    REVIEW.
    """
    if not pdp_width_cm or image_px_width == 0:
        return []
    px_per_mm = image_px_width / (pdp_width_cm * 10.0)
    unit_tokens = {"g", "gm", "gms", "kg", "ml", "l", "mg", "n", "nos", "pcs", "pieces"}
    heights = []
    for idx, w in enumerate(words):
        token = w.text.lower().strip(".,:")
        looks_like_qty = any(ch.isdigit() for ch in token) or token in unit_tokens
        # also catch neighbours of a "net wt/qty" label
        if looks_like_qty:
            heights.append(w.height / px_per_mm)
    return heights
