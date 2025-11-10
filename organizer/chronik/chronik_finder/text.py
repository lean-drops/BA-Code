#!/usr/bin/env python3
"""
OCR-starker Text-Extractor für Chroniken-PDFs.

Pipeline:
  1) PyMuPDF-Varianten: "text" → "blocks" → "rawdict" (bestes Ergebnis via Qualitätsheuristik)
  2) Fallbacks: pdfplumber → pypdf → pdfminer.six
  3) OCR-Stack mit Fraktur-Fokus:
     - Rendering mit hoher Auflösung (600 dpi)
     - Orientierungserkennung (Tesseract OSD)
     - Vorverarbeitung: Entzerrung (Deskew), Entrauschen, adaptive Binarisierung
     - Mehrere Tesseract-Läufe mit frk/deu_frak+deu+lat, OEM 1, PSM 6/4/12, Auswahl per Score
     - Optionaler weiterer OCR-Fallback: EasyOCR (falls installiert)
  4) Normalisierung historischer Typographie: Lang-s, Ligaturen, Diakritika-Faltung, Silbentrennungen

Schnittstellen bleiben stabil:
  - normalize_text(txt: str) -> str
  - extract_text(page, pdf_path: str, page_index: int, pdf_mtime: float) -> str

Assumptions:
  - .env-Modul exportiert Flags (HAVE_PDFMINER, HAVE_PDFPLUMBER, HAVE_PYPDF, HAVE_TESS) und optionale Objekte.
  - Zusätzliche Libraries (cv2, numpy, skimage, easyocr) sind optional und werden sicher erkannt.
  - Kein CLI, feste Defaults, sichtbare Debug-Prints.

Usage:
  - Wird vom Scanner importiert. Direktstart führt einen No-Op-Selbsttest mit Debug-Ausgabe aus.
"""
from __future__ import annotations

import io
import math
import unicodedata
from typing import Any, Iterable, List, Tuple, Optional

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
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def _merge_hyphenation(txt: str) -> str:
    # Worttrennung über Zeilen: "Wort-\nfortsetzung" -> "Wortfortsetzung"
    import re as _re
    txt = _re.sub(r"([A-Za-zÄÖÜäöüß]{2,})-\s*\n\s*([a-zäöüß]{2,})", r"\1\2", txt)
    return txt


def normalize_text(txt: str) -> str:
    if not txt:
        return ""
    txt = unicodedata.normalize("NFC", txt)

    subs = {
        "\u00AD": "",    # Soft Hyphen
        "ſ": "s",
        "ꝛ": "r", "ꝝ": "v", "ꝯ": "o",
        "Æ": "AE", "æ": "ae", "Œ": "OE", "œ": "oe",
        "ﬃ": "ffi", "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬅ": "ft", "ﬆ": "st",
    }
    for k, v in subs.items():
        txt = txt.replace(k, v)

    txt = _merge_hyphenation(txt)

    import re as _re
    txt = _re.sub(r"\r\n?|\n", " ", txt)
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
    score *= math.log1p(len(t)) / math.log(1 + 400)  # etwas längenfreundlicher
    return score


def _is_likely_scanned(first_try: str, page: Any) -> bool:
    """Heuristik: sehr kurzer/leer extrahierter Text und zugleich Bildanteil → gescannt."""
    try:
        if first_try and len(first_try) >= 50:
            return False
        # Wenn viele "image" Blöcke existieren, spricht das für Scan
        raw = page.get_text("rawdict")
        if isinstance(raw, dict):
            blocks = raw.get("blocks", [])
            img_blocks = sum(1 for b in blocks if "image" in b)
            return img_blocks >= 1
    except Exception:
        pass
    return not first_try or len(first_try) < 20


# ------------------------ PyMuPDF-Varianten ------------------------

def _mupdf_text_variants(page: Any) -> List[str]:
    variants: List[str] = []

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
        lines: List[str] = []
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

    variants = [v for v in variants if v]
    if not variants:
        return []
    return sorted(variants, key=lambda s: (_text_quality(s), len(s)), reverse=True)


# ------------------------ Bildaufbereitung ------------------------

def _render_page_image(page: Any, dpi: int = 600):
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    from PIL import Image
    return Image.open(io.BytesIO(pix.tobytes("png")))


def _rotate_pil(img):
    if not HAVE_TESS or pytesseract is None:
        return img
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


def _deskew_binarize(img):
    """
    Deskew + Binarisierung. OpenCV/Skimage bevorzugt, sonst PIL-Fallback.
    """
    try:
        import numpy as _np
        import cv2 as _cv2
        arr = _np.array(img.convert("L"))

        # Leicht entrauschen
        try:
            arr = _cv2.fastNlMeansDenoising(arr, None, 7, 7, 21)
        except Exception:
            pass

        # Schwellwert adaptiv (Sauvola falls verfügbar)
        try:
            from skimage.filters import threshold_sauvola  # type: ignore
            thresh = threshold_sauvola(arr, window_size=35, k=0.2)
            bin_img = (arr > thresh).astype("uint8") * 255
        except Exception:
            bin_img = _cv2.adaptiveThreshold(arr, 255, _cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                             _cv2.THRESH_BINARY, 35, 11)

        # Deskew via Hough-Linien
        angle = 0.0
        try:
            edges = _cv2.Canny(bin_img, 50, 150, apertureSize=3)
            lines = _cv2.HoughLines(edges, 1, _np.pi / 180, 200)
            if lines is not None and len(lines) > 0:
                angles = []
                for rho_theta in lines[:200]:
                    for rho, theta in _np.atleast_2d(rho_theta):
                        ang = theta * 180.0 / _np.pi
                        if 20 < ang < 160:  # horizontnahe ignorieren, Fokus auf Text-Linien
                            angles.append(ang - 90)
                if angles:
                    angle = float(_np.median(angles))
        except Exception:
            angle = 0.0

        if abs(angle) > 0.3:
            (h, w) = bin_img.shape
            M = _cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
            bin_img = _cv2.warpAffine(bin_img, M, (w, h), flags=_cv2.INTER_CUBIC, borderMode=_cv2.BORDER_REPLICATE)

        # Leichte Morphologie, um Gebrochene-Schrift zu schließen
        try:
            kernel = _cv2.getStructuringElement(_cv2.MORPH_RECT, (1, 1))
            bin_img = _cv2.morphologyEx(bin_img, _cv2.MORPH_OPEN, kernel, iterations=1)
        except Exception:
            pass

        rgb = _cv2.cvtColor(bin_img, _cv2.COLOR_GRAY2RGB)
        return rgb
    except Exception:
        # PIL-Fallback
        from PIL import ImageOps
        g = img.convert("L")
        g = ImageOps.autocontrast(g)
        g = g.point(lambda p: 255 if p > 170 else 0)
        return g.convert("RGB")


# ------------------------ OCR-Engines ------------------------

def _ocr_tesseract(img, langs: str, psm: int) -> str:
    if not HAVE_TESS or pytesseract is None:
        return ""
    cfg = f"--oem 1 --psm {psm}"
    try:
        return pytesseract.image_to_string(img, lang=langs, config=cfg) or ""
    except Exception:
        # Fallback ohne Fraktur-Pakete
        try:
            base = "deu+lat+eng"
            return pytesseract.image_to_string(img, lang=base, config=cfg) or ""
        except Exception:
            return ""


def _ocr_easyocr(img) -> str:
    """
    Optionaler Fallback via EasyOCR (falls installiert).
    Hinweis: EasyOCR hat kein spezifisches Frakturmodell, kann aber bei schwachem Druck helfen.
    """
    try:
        import numpy as _np
        import easyocr  # type: ignore
        arr = _np.array(img)
        reader = easyocr.Reader(["de", "en"], gpu=False)  # CPU-default
        lines = reader.readtext(arr, detail=0, paragraph=True)
        text = "\n".join(lines)
        return text
    except Exception:
        return ""


def _ocr_page(page: Any) -> str:
    """
    OCR mehrstufig mit starker Fraktur-Unterstützung.
    """
    try:
        from PIL import Image
    except Exception:
        return ""

    try:
        base = _render_page_image(page, dpi=600)
    except Exception as e:
        print(f"[WARN] Rendering fehlgeschlagen: {e}")
        return ""

    # Orientierung und Vorverarbeitung
    rot = _rotate_pil(base)
    pre = _deskew_binarize(rot)

    # Tesseract-Laufbündel
    # Sprachen: frk (Fraktur), deu_frak (kompatibles Paket), deu, lat, eng
    lang_chain = ["frk+deu_frak+deu+lat+eng", "deu_frak+deu+lat+eng", "deu+lat+eng"]
    psm_chain = [6, 4, 12]  # 6: block of text, 4: column, 12: sparse
    candidates: List[Tuple[str, float]] = []

    for langs in lang_chain:
        for psm in psm_chain:
            t = _ocr_tesseract(pre, langs, psm=psm)
            t = normalize_text(t)
            if t:
                score = _text_quality(t)
                candidates.append((t, score))
                print(f"[DEBUG] OCR tesseract langs={langs} psm={psm} score={score:.3f} len={len(t)}")
                if score >= 0.65 and len(t) >= TEXT_MIN_LEN:
                    # gut genug, früh abbrechen
                    return t

    # Optionaler Fallback: EasyOCR, wenn Tesseract schwach war
    if not candidates or max(s for _, s in candidates) < 0.45:
        e_txt = _ocr_easyocr(pre)
        e_txt = normalize_text(e_txt)
        if e_txt:
            score = _text_quality(e_txt)
            candidates.append((e_txt, score))
            print(f"[DEBUG] OCR easyocr score={score:.3f} len={len(e_txt)}")

    if not candidates:
        return ""

    # bestes Ergebnis wählen
    best = max(candidates, key=lambda x: (x[1], len(x[0])))[0]
    return best


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
    first_try = ""
    try:
        variants = _mupdf_text_variants(page)
        if variants:
            first_try = variants[0]
            for cand in variants:
                if len(cand) > len(best):
                    best = cand
                if _text_quality(cand) >= 0.65 and len(cand) >= TEXT_MIN_LEN:
                    best = cand
                    break
    except Exception:
        pass
    if len(best) >= TEXT_MIN_LEN and _text_quality(best) >= 0.6:
        cache.put(pdf_path, page_index, pdf_mtime, best)
        return best

    # 2) pdfplumber
    if HAVE_PDFPLUMBER and pdfplumber is not None:
        try:
            with pdfplumber.open(pdf_path) as pl:
                if 0 <= page_index < len(pl.pages):
                    t2 = pl.pages[page_index].extract_text() or ""
                    t2 = normalize_text(t2)
                    if len(t2) > len(best):
                        best = t2
        except Exception:
            pass
        if len(best) >= TEXT_MIN_LEN and _text_quality(best) >= 0.55:
            cache.put(pdf_path, page_index, pdf_mtime, best)
            return best

    # 3) pypdf
    if HAVE_PYPDF and pypdf is not None:
        try:
            reader = pypdf.PdfReader(pdf_path)
            if 0 <= page_index < len(reader.pages):
                t3 = reader.pages[page_index].extract_text() or ""
                t3 = normalize_text(t3)
                if len(t3) > len(best):
                    best = t3
        except Exception:
            pass
        if len(best) >= TEXT_MIN_LEN and _text_quality(best) >= 0.55:
            cache.put(pdf_path, page_index, pdf_mtime, best)
            return best

    # 4) pdfminer (Dokumentweit; kann langsam sein)
    # Nur wenn nicht klar gescannt, sonst direkt OCR versuchen
    likely_scan = _is_likely_scanned(first_try or best, page)
    if HAVE_PDFMINER and pdfminer_extract_text is not None and not likely_scan:
        try:
            full = pdfminer_extract_text(pdf_path) or ""
            full = normalize_text(full)
            if len(full) > len(best):
                best = full
        except Exception:
            pass
        if len(best) >= TEXT_MIN_LEN and _text_quality(best) >= 0.55:
            cache.put(pdf_path, page_index, pdf_mtime, best)
            return best

    # 5) OCR bevorzugen, wenn gescannt oder Text zu schwach
    try:
        ocr = _ocr_page(page)
    except Exception as e:
        print(f"[WARN] OCR fehlgeschlagen: {e}")
        ocr = ""
    if len(ocr) > len(best):
        best = normalize_text(ocr)

    cache.put(pdf_path, page_index, pdf_mtime, best)
    return best


# ------------------------ Debug-Selbsttest ------------------------

def main() -> None:
    print("[DEBUG] text.py self-test: Kein CLI, nur Import-Test und Konstanten.")
    try:
        print(f"[DEBUG] TEXT_MIN_LEN={TEXT_MIN_LEN}")
        print(f"[DEBUG] HAVE_TESS={HAVE_TESS}  HAVE_PDFPLUMBER={HAVE_PDFPLUMBER}  "
              f"HAVE_PYPDF={HAVE_PYPDF}  HAVE_PDFMINER={HAVE_PDFMINER}")
    except Exception as e:
        print(f"[ERROR] Self-test failed: {e}")


if __name__ == "__main__":
    main()