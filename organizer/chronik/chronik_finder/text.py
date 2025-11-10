#!/usr/bin/env python3
"""
Text-Normalisierung und mehrstufige Extraktion mit OCR-Fallback.
Reihenfolge: PyMuPDF → pdfplumber → pypdf → pdfminer → OCR.

Verbesserungen:
- Aggressive Normalisierung für Frühdrucke: Lang-s (ſ), Ligaturen, Æ/Œ, Diakritika-Faltung.
- PyMuPDF-Mehrwege: 'text' → sortierte 'blocks' → rekonstruiertes 'rawdict'.
- Qualitätsheuristik zur Entscheidung über OCR.
- OCR-Preprocessing: Entzerrung, Binarisierung, adaptive Schwelle, Orientation/Rotation.
- Tesseract mit Fraktur-Model („deu_frak“) und Latein; zwei PSM-Pässe.
- Cache bleibt unverändert.
"""
from __future__ import annotations

import io
import math
import unicodedata
from typing import Any

from .constants import TEXT_MIN_LEN
from .env import (
    HAVE_PDFMINER,
    HAVE_PDFPLUMBER,
    HAVE_PYPDF,
    HAVE_TESS,
    pdfminer_extract_text,
    pdfplumber,
    pypdf,
    pytesseract,
)
from .cache import get_page_cache


# ------------------------ Normalisierung ------------------------

def normspace(s: str) -> str:
    import re as _re
    return _re.sub(r"\s+", " ", s).strip()


def _strip_diacritics(s: str) -> str:
    # diakritika entfernen, aber ß und å/ø/æ bewusst handhaben
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def normalize_text(txt: str) -> str:
    if not txt:
        return ""
    txt = unicodedata.normalize("NFC", txt)

    # Häufige Altzeichen
    subs = {
        "\u00AD": "",    # Soft Hyphen
        "ſ": "s",
        "ꝛ": "r", "ꝝ": "v", "ꝯ": "o",
        "Æ": "AE", "æ": "ae", "Œ": "OE", "œ": "oe",
        "ﬃ": "ffi", "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬅ": "ft", "ﬆ": "st",
    }
    for k, v in subs.items():
        txt = txt.replace(k, v)

    # Silbentrennung: Wort-Wort\nwort → WortWortwort
    import re as _re
    txt = _re.sub(r"([A-Za-zÄÖÜäöüß]{2,})-\s*\n\s*([a-zäöüß]{2,})", r"\1\2", txt)
    txt = _re.sub(r"\r\n?|\n", " ", txt)

    # diakritika leicht glätten für robustere Suchen
    txt = _strip_diacritics(txt)
    return normspace(txt)


# ------------------------ Heuristiken ------------------------

def _text_quality(t: str) -> float:
    if not t:
        return 0.0
    letters = sum(ch.isalpha() for ch in t)
    digits = sum(ch.isdigit() for ch in t)
    frac_letters = letters / max(1, len(t))
    score = (frac_letters * 0.7) + (min(0.2, digits / max(1, len(t))) * 0.3)
    score *= math.log1p(len(t)) / math.log(1 + 200)  # sanfte Länge
    return score


# ------------------------ PyMuPDF-Varianten ------------------------

def _mupdf_text_variants(page: Any) -> list[str]:
    variants: list[str] = []

    try:
        t0 = page.get_text("text") or ""
        variants.append(normalize_text(t0))
    except Exception:
        pass

    try:
        # blocks: [(x0,y0,x1,y1,text, block_no, ...)]
        blocks = page.get_text("blocks") or []
        blocks = [b for b in blocks if len(b) >= 5 and isinstance(b[4], str) and b[4].strip()]
        blocks.sort(key=lambda b: (round(b[1], 1), round(b[0], 1)))  # y, dann x
        t1 = " ".join(b[4] for b in blocks)
        variants.append(normalize_text(t1))
    except Exception:
        pass

    try:
        raw = page.get_text("rawdict")
        lines: list[str] = []
        if isinstance(raw, dict):
            for blk in raw.get("blocks", []):
                for line in blk.get("lines", []):
                    span_texts = [sp.get("text", "") for sp in line.get("spans", [])]
                    if any(span_texts):
                        lines.append("".join(span_texts))
        t2 = " ".join(lines)
        variants.append(normalize_text(t2))
    except Exception:
        pass

    # längstes/qualitativ bestes wählen
    variants = [v for v in variants if v]
    if not variants:
        return []
    return sorted(variants, key=lambda s: (_text_quality(s), len(s)), reverse=True)


# ------------------------ OCR ------------------------

def _rotate_pil(img):
    try:
        osd = pytesseract.image_to_osd(img)
        import re as _re
        m = _re.search(r"Rotate:\s+(\d+)", osd)
        deg = int(m.group(1)) if m else 0
        if deg % 360:
            return img.rotate(360 - deg, expand=True)
    except Exception:
        pass
    return img


def _prep_for_ocr(img):
    # Optionale OpenCV-Verbesserungen, sonst PIL-Fallback
    try:
        import numpy as _np
        import cv2 as _cv2
        arr = _np.array(img.convert("L"))
        # leichte Entzerrung/Schwellung
        arr = _cv2.bilateralFilter(arr, 7, 50, 50)
        arr = _cv2.adaptiveThreshold(arr, 255, _cv2.ADAPTIVE_THRESH_GAUSSIAN_C, _cv2.THRESH_BINARY, 35, 11)
        # Deskew via Moment
        coords = _np.column_stack(_np.where(arr < 128))
        angle = 0.0
        if len(coords) > 0:
            from sklearn.linear_model import RANSACRegressor  # optional; falls nicht vorhanden, except
            try:
                # einfacher Schätzer aus Koordinaten
                xs = coords[:, 1].reshape(-1, 1)
                ys = coords[:, 0]
                r = RANSACRegressor().fit(xs, ys)
                slope = r.estimator_.coef_[0] if hasattr(r, "estimator_") else 0.0
                angle = -math.degrees(math.atan(slope))
            except Exception:
                pass
        if abs(angle) > 0.5:
            (h, w) = arr.shape
            M = _cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
            arr = _cv2.warpAffine(arr, M, (w, h), flags=_cv2.INTER_CUBIC, borderMode=_cv2.BORDER_REPLICATE)
        return _cv2.cvtColor(arr, _cv2.COLOR_GRAY2RGB)
    except Exception:
        # PIL: einfache Binarisierung
        from PIL import ImageOps
        g = img.convert("L")
        g = ImageOps.autocontrast(g)
        g = g.point(lambda p: 255 if p > 180 else 0)
        return g.convert("RGB")


def _ocr_page(page: Any) -> str:
    if not HAVE_TESS or pytesseract is None:
        return ""
    try:
        pix = page.get_pixmap(dpi=400)  # höheres DPI für Fraktur
        from PIL import Image
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        img = _rotate_pil(img)
        img = _prep_for_ocr(img)

        langs = "deu+deu_frak+lat+eng"
        config1 = "--oem 1 --psm 6"
        config2 = "--oem 1 --psm 4"
        try:
            t1 = pytesseract.image_to_string(img, lang=langs, config=config1) or ""
        except Exception:
            # falls deu_frak fehlt
            t1 = pytesseract.image_to_string(img, lang="deu+lat+eng", config=config1) or ""

        # zweiter Pass falls mager
        if len(t1) < 80:
            try:
                t2 = pytesseract.image_to_string(img, lang=langs, config=config2) or ""
            except Exception:
                t2 = pytesseract.image_to_string(img, lang="deu+lat+eng", config=config2) or ""
            if len(t2) > len(t1):
                t1 = t2
        return t1
    except Exception:
        return ""


# ------------------------ Extraktion mit Kaskade + Cache ------------------------

def extract_text(page: Any, pdf_path: str, page_index: int, pdf_mtime: float) -> str:
    """
    Extrahiert Page-Text mit Kaskade und Cache.
    """
    cache = get_page_cache()
    cached = cache.get(pdf_path, page_index, pdf_mtime)
    if cached is not None and len(cached) >= 1:
        return cached

    # 1) PyMuPDF in Varianten
    best = ""
    try:
        for cand in _mupdf_text_variants(page):
            if len(cand) > len(best):
                best = cand
            if _text_quality(cand) >= 0.6 and len(cand) >= TEXT_MIN_LEN:
                best = cand
                break
    except Exception:
        pass
    if len(best) >= TEXT_MIN_LEN:
        cache.put(pdf_path, page_index, pdf_mtime, best)
        return best

    # 2) pdfplumber
    if HAVE_PDFPLUMBER and pdfplumber is not None:
        try:
            with pdfplumber.open(pdf_path) as pl:
                if page_index < len(pl.pages):
                    t2 = pl.pages[page_index].extract_text() or ""
                    t2 = normalize_text(t2)
                    if len(t2) > len(best):
                        best = t2
        except Exception:
            pass
        if len(best) >= TEXT_MIN_LEN:
            cache.put(pdf_path, page_index, pdf_mtime, best)
            return best

    # 3) pypdf
    if HAVE_PYPDF and pypdf is not None:
        try:
            reader = pypdf.PdfReader(pdf_path)
            if page_index < len(reader.pages):
                t3 = reader.pages[page_index].extract_text() or ""
                t3 = normalize_text(t3)
                if len(t3) > len(best):
                    best = t3
        except Exception:
            pass
        if len(best) >= TEXT_MIN_LEN:
            cache.put(pdf_path, page_index, pdf_mtime, best)
            return best

    # 4) pdfminer (Dokumentweit; als Verbesserung, falls vorhanden)
    if HAVE_PDFMINER and pdfminer_extract_text is not None:
        try:
            full = pdfminer_extract_text(pdf_path) or ""
            full = normalize_text(full)
            if len(full) > len(best):
                best = full
        except Exception:
            pass
        if len(best) >= TEXT_MIN_LEN:
            cache.put(pdf_path, page_index, pdf_mtime, best)
            return best

    # 5) OCR
    ocr = _ocr_page(page)
    if len(ocr) > len(best):
        best = normalize_text(ocr)

    cache.put(pdf_path, page_index, pdf_mtime, best)
    return best