# organizer/chronik/chronik_finder/scan.py
#!/usr/bin/env python3
"""
Dokumenten-Scan (PDF + TXT) mit robuster OCR-Unterstützung.

Verbesserungen:
- Unterstützung für .txt-Dateien (schnelles Plain-Text-Scanning).
- Kontextauswahl an Wortgrenzen und Satzzeichen.
- Robustere Textqualitätsprüfung, inkl. digit-lastiger Seiten (Tabellen/Rechnungen).
- Früheres Überspringen wirklich leerer Seiten.
- Stabilerer Umgang mit OCR-/Alttext durch verbesserte extract_text()-Kaskade.
- CPU-freundliche Parallelisierung mit Fallback pro Seite.
- Weniger IO-Overhead: Debug-Ausgaben sind optional (CHRONIK_DEBUG=1).
"""
from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from .constants import DEFAULT_SKIP_BIBLIOGRAPHY, SNIPPET_LEN
from .env import fitz
from .models import Hit, PatternEntry
from .patterns import detect_bibliography_pages
from .text import extract_text

# ------------------------ Debug-Flag ------------------------

DEBUG = bool(os.environ.get("CHRONIK_DEBUG"))


def _debug(msg: str) -> None:
    if DEBUG:
        print(msg)


# ------------------------ Kontext-Helfer ------------------------

def _slice_to_word_boundaries(text: str, start: int, end: int, budget: int) -> Tuple[int, int]:
    """
    Schneidet um das Match herum an Wortgrenzen, maximal mit 'budget' Zeichen.

    Args:
        text: Volltext der Seite.
        start: Startindex des Matches.
        end: Endindex des Matches.
        budget: maximale Kontextlänge.

    Returns:
        (L, R) als Indizes in text.
    """
    half = max(10, budget // 2)
    center = (start + end) // 2
    L = max(0, center - half)
    R = min(len(text), L + budget)

    while L > 0 and text[L - 1].isalnum():
        L -= 1
    while R < len(text) and text[R:R + 1].isalnum():
        R += 1
    return L, R


def make_context(text: str, span: Tuple[int, int], length: int = SNIPPET_LEN) -> str:
    """
    Baut einen Kontext-Snippet um ein Match herum.

    Args:
        text: Volltext der Seite.
        span: (start, end) des Matches.
        length: Ziel-Länge des Kontextes.

    Returns:
        Kontextstring, intern whitespace-normalisiert.
    """
    s, e = span
    L, R = _slice_to_word_boundaries(text, s, e, max(40, length))
    return " ".join(text[L:R].split())


# ------------------------ Kernscan ------------------------

def _good_enough(txt: str) -> bool:
    """
    Grobe Qualitätsheuristik für Seiten/Dateien.

    - akzeptiert klassische Fließtexte (viele Buchstaben).
    - akzeptiert auch zahlenlastige Seiten (Tabellen/Rechnungen), wenn genügend Inhalt.
    """
    if not txt:
        return False

    length = len(txt)
    if length < 30:
        return False

    letters = 0
    digits = 0
    for ch in txt:
        if ch.isalpha():
            letters += 1
        elif ch.isdigit():
            digits += 1

    # Klassischer Fließtext: viele Buchstaben
    if letters >= max(10, length // 8):
        return True

    # Tabellen / Rechnungen: viele Ziffern, aber wenig Buchstaben
    if digits >= 20 and (letters + digits) >= max(25, length // 4):
        return True

    return False


def scan_pdf(
    pdf_path: Path,
    patterns: List[PatternEntry],
    skip_bib: bool = DEFAULT_SKIP_BIBLIOGRAPHY,
) -> List[Hit]:
    """
    Scannt ein einzelnes PDF nach den gegebenen Pattern in allen Seiten.

    OCR und Text-Extractor werden intern über extract_text() gekapselt.
    """
    hits: List[Hit] = []
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        print(f"[WARN] Konnte PDF nicht öffnen: {pdf_path} ({e})")
        return hits

    try:
        mtime = pdf_path.stat().st_mtime if pdf_path.exists() else 0.0
        bib_pages = detect_bibliography_pages(doc) if skip_bib else set()

        page_count = doc.page_count
        for pidx in range(page_count):
            if skip_bib and pidx in bib_pages:
                _debug(f"[DEBUG] Überspringe bekannte Bibliographie-Seite {pidx + 1} in {pdf_path}")
                continue

            page = doc.load_page(pidx)
            txt = extract_text(page, str(pdf_path), pidx, mtime)

            if not _good_enough(txt):
                _debug(f"[DEBUG] Seite zu schwach / leer: {pdf_path} p{pidx + 1} len={len(txt)}")
                continue

            for pe in patterns:
                for m in pe.regex.finditer(txt):
                    s, e = m.span()
                    ctx = make_context(txt, (s, e))
                    hits.append(Hit(
                        pdf_path=str(pdf_path),
                        page=pidx + 1,
                        group=pe.group,
                        label=pe.label,
                        pattern=pe.regex.pattern,
                        context=ctx,
                    ))
    finally:
        doc.close()
    return hits


def scan_txt(txt_path: Path, patterns: List[PatternEntry]) -> List[Hit]:
    """
    Schneller Scan einer .txt-Datei (kein OCR, kein Seitensplit).

    Behandelt die komplette Datei als eine „Seite“.
    """
    hits: List[Hit] = []
    try:
        # Schnelles Einlesen, robust gegen Encoding-Probleme
        txt = txt_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        print(f"[WARN] Konnte TXT nicht lesen: {txt_path} ({e})")
        return hits

    if not _good_enough(txt):
        _debug(f"[DEBUG] TXT-Datei zu schwach / leer: {txt_path} len={len(txt)}")
        return hits

    for pe in patterns:
        for m in pe.regex.finditer(txt):
            s, e = m.span()
            ctx = make_context(txt, (s, e))
            hits.append(Hit(
                pdf_path=str(txt_path),
                page=1,
                group=pe.group,
                label=pe.label,
                pattern=pe.regex.pattern,
                context=ctx,
            ))
    return hits


def scan_document(
    path: Path,
    patterns: List[PatternEntry],
    skip_bib: bool = DEFAULT_SKIP_BIBLIOGRAPHY,
) -> List[Hit]:
    """
    Router-Funktion: wählt je nach Dateityp den passenden Scanner.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return scan_pdf(path, patterns, skip_bib=skip_bib)
    elif suffix in {".txt"}:
        # skip_bib ist hier irrelevant, wird ignoriert
        return scan_txt(path, patterns)
    else:
        _debug(f"[DEBUG] Unbekannter Dateityp, übersprungen: {path}")
        return []


# ---------- Parallel-Scan ----------

def _serialize_patterns(patterns: Sequence[PatternEntry]) -> List[Tuple[str, str, str]]:
    return [(p.label, p.group, p.regex.pattern) for p in patterns]


def _compile_local(serialized: Sequence[Tuple[str, str, str]]) -> List[PatternEntry]:
    import re as _re
    loc: List[PatternEntry] = []
    for label, group, pat in serialized:
        try:
            loc.append(PatternEntry(label=label, group=group, regex=_re.compile(pat, _re.IGNORECASE)))
        except Exception as exc:
            print(f"[WARN] Konnte Regex nicht kompilieren ({label}/{group}): {exc}")
            continue
    return loc


def _worker_scan(
    path_str: str,
    patterns_serialized: Sequence[Tuple[str, str, str]],
    skip_bib: bool,
) -> List[Hit]:
    from pathlib import Path as _Path
    path = _Path(path_str)
    pats = _compile_local(patterns_serialized)
    return scan_document(path, pats, skip_bib=skip_bib)


def scan_pdfs(
    pdfs: Sequence[Path],
    patterns: List[PatternEntry],
    max_workers: int = 0,
    skip_bib: bool = DEFAULT_SKIP_BIBLIOGRAPHY,
) -> List[Hit]:
    """
    Scannt eine Menge Dokumente (PDFs und TXTs), optional parallel.

    Args:
        pdfs: Liste von Pfaden (historischer Name, jetzt PDFs + TXTs).
        patterns: Liste von PatternEntry (Label, Gruppe, Regex).
        max_workers: 0 → Auto (bis 4), 1 → sequentiell, >1 → feste Prozesszahl.
        skip_bib: Bibliographie-Seiten heuristisch überspringen (nur für PDFs relevant).

    Returns:
        Liste aller Hits über alle Dokumente.
    """
    pdfs = list(pdfs)
    n_docs = len(pdfs)

    # Kleine Mengen sequentiell; Parallelisierung lohnt sich sonst nicht
    if n_docs < 4 or max_workers == 1:
        out: List[Hit] = []
        for i, p in enumerate(pdfs, 1):
            print(f"[INFO] ({i}/{n_docs}) Verarbeite: {p}")
            hs = scan_document(p, patterns, skip_bib=skip_bib)
            print(f"       Treffer: {len(hs)}")
            out.extend(hs)
        return out

    import multiprocessing
    if max_workers <= 0:
        cpu = max(1, (os.cpu_count() or 2) - 1)
        max_workers = min(cpu, 4)

    print(f"[INFO] Parallel-Scan mit {max_workers} Prozessen …")
    out: List[Hit] = []
    ser = _serialize_patterns(patterns)

    with ProcessPoolExecutor(
        max_workers=max_workers,
        mp_context=multiprocessing.get_context("spawn"),
    ) as ex:
        futures = {ex.submit(_worker_scan, str(p), ser, skip_bib): p for p in pdfs}
        done = 0
        total = len(futures)
        for fut in as_completed(futures):
            p = futures[fut]
            try:
                hs = fut.result()
                out.extend(hs)
                done += 1
                print(f"[INFO] ({done}/{total}) Fertig: {p} | Treffer: {len(hs)}")
            except Exception as e:
                print(f"[ERROR] Worker-Fehler bei {p}: {e}. Fallback sequentiell.")
                try:
                    hs = scan_document(p, patterns, skip_bib=skip_bib)
                    out.extend(hs)
                except Exception as e2:
                    print(f"[ERROR] Fallback fehlgeschlagen bei {p}: {e2}")
    return out