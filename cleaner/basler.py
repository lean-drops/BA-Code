"""
Roeteler-Chronik-Stripper — behält nur den Chronisten, normalisiert den Text.

Dependencies:
  ftfy>=6.2.0
  regex>=2023.10.3

Ziel
----
- Extrahiere ausschließlich den erzählenden Chroniktext (keine Kommentare, keine Links, kein Apparat).
- Normalisiere Unicode und Orthographie-Glitches, entknüpfe Silbentrennungen, reflowe Absätze.

Vorgaben
--------
Input : "./Basler Chroniken.txt"
Output: "./output/roeteler_chronik_clean.txt"
Keine CLI-Argumente, ein Durchlauf, sichtbare Debug-Ausgaben.

Verwendung
----------
1) Rohtext als "Basler Chroniken.txt" in das Arbeitsverzeichnis legen.
2) Dieses Skript starten.
3) Ergebnis liegt unter ./output/roeteler_chronik_clean.txt
"""

from __future__ import annotations

import os
import re
import regex
from dataclasses import dataclass
from typing import List, Tuple

try:
    from ftfy import fix_text
except Exception as exc:
    raise ImportError("Installiere Abhängigkeit: pip install ftfy>=6.2.0") from exc


# --------------------------- Exceptions ---------------------------

class CleaningError(Exception):
    """Fatale Probleme beim Säubern."""


# --------------------------- Pfade ---------------------------

INPUT_PATH = "/Users/programming/PycharmProjects/Find_Bibliography_NEw/cleaner/Basler Chroniken.txt"
OUTPUT_DIR = "/Users/programming/PycharmProjects/Find_Bibliography_NEw/data/azk_library/test"
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "roeteler_chronik_clean.txt")


# --------------------------- Muster & Heuristiken ---------------------------

YEAR_PAT = regex.compile(r"\b(13|14)\d{2}\b")
# Startanker, die Chronik-Absätze typischerweise beginnen
START_TOKENS = regex.compile(
    r"^(?:Anno|An\.|Item|Im\s+jare|Im\s+jar|In\s+dem\s+jare|In\s+dem\s+jar|Des\s+jares?|Des\s+jare|Do|Uff|Darnach|Hie|Also|Nach\s+dem|Darauf)\b",
    flags=regex.IGNORECASE,
)

# Lexik, die für die Chronik typisch ist (wertet Scoring auf)
ARCHAIC_TOKENS = [
    r"\bdo\b", r"\buff\b", r"\bze\b", r"\bhertzog", r"\bmarkgraf", r"\bbischof",
    r"\britter", r"\bknecht", r"\bgräf", r"\bgräve", r"\bburg\b", r"\bstatt\b",
    r"\bstett", r"\bküng", r"\bpapst", r"\bconcil", r"\bconcilium", r"\bhussit",
    r"\bfehde", r"\bfasnacht", r"\bjar", r"\bjare",
]
ARCHAIC_RE = regex.compile("|".join(ARCHAIC_TOKENS), flags=regex.IGNORECASE)

# Eindeutig editorische Signale (hartes Ausschlusskriterium)
EDITORIAL_TOKENS = [
    r"\bEinleitung\b", r"\bVorrede\b", r"\bBeilage(n)?\b", r"\bRegister\b",
    r"\bInhaltsverzeichn", r"\bAnalekten\b", r"\bVariante(n)?\b", r"\bAnmerkung(en)?\b",
    r"\bDigitized\s+by\s+Google\b", r"\bBASLER\s+CHRONIKEN\b",
    r"\bWurstisen\b", r"\bTrouillat\b", r"\bHegel\b", r"\bStuder\b", r"\bMone\b",
    r"\bCod\.\b", r"\bMs\.\b|\bHs\.\b|\bHss\.\b", r"\bUrk\.\b|\bUrkundenb\.\b|\bUrk\b",
    r"\bSt\.\s*A\.\b", r"\bBd\.\b", r"\bS\.\s*\d+\b", r"\bAusg\.\b",
    r"\bBibliothek\b", r"\bTop\.\b", r"\bAnnalen\b", r"\bChron\.\b",
]
EDITORIAL_RE = regex.compile("|".join(EDITORIAL_TOKENS), flags=regex.IGNORECASE)

# Zeilen, die als Seitenmobiliar gelten und vollständig fallen
PAGE_FURNITURE_LINES = [
    r"^\s*\d+\s*$",                    # reine Zahl
    r"^\s*[IVXLCDM]{1,8}\s*$",         # römische Zahl
    r"^\s*[—\-–]+\s*$",                # Linie
    r"^\s*[A-ZÄÖÜ][A-ZÄÖÜ\s\.\-]{2,40}$",  # kurze Allcaps-Header
]
PAGE_FURNITURE_RES = [regex.compile(p) for p in PAGE_FURNITURE_LINES]

# Inline-Klammerkram und Widgets entfernen
INLINE_BRACKETS = [
    r"\[[^\]\n]{1,200}\]",        # [ ... ]
    r"⟪[^⟫\n]{1,200}⟫",
    r"«[^»\n]{1,200}»",
    r"]*",                  # UI-Marker
]
INLINE_BRACKETS_RE = [regex.compile(p) for p in INLINE_BRACKETS]

# Apparatartige Klammern (runden) nur wenn Zitatcharakter
PAREN_APPARAT = [
    r"\([^)]*\bAnm\.[^)]*\)", r"\([^)]*\bVgl\.[^)]*\)", r"\([^)]*\bUrk[^)]*\)",
    r"\([^)]*\bS\.\s*\d+[^)]*\)", r"\([^)]*\bBd\.[^)]*\)", r"\([^)]*\bChron\.[^)]*\)",
    r"\([^)]*\d{3,4}[^)]*\)",   # Zahlenlastig
]
PAREN_APPARAT_RE = [regex.compile(p) for p in PAREN_APPARAT]

# Fußnotenmarker
FOOTNOTE_MARKERS_RE = [
    regex.compile(r"\b\d+\)"),              # 1)
    regex.compile(r"\(\d+\)"),              # (1)
    regex.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+"),        # Hochzahlen
    regex.compile(r"\^\d+"),                # ^12
    regex.compile(r"(?<=\w)\d{1,3}(?=[\s,.;:])"),  # angehängte kleine Ziffern
]


@dataclass
class Stats:
    paragraphs_total: int = 0
    paragraphs_kept: int = 0
    paragraphs_dropped_editorial: int = 0
    paragraphs_dropped_low_score: int = 0
    chars_in: int = 0
    chars_out: int = 0


# --------------------------- Kernfunktionen ---------------------------

def read_text(path: str) -> str:
    if not os.path.exists(path):
        raise CleaningError(f"Eingabedatei nicht gefunden: {path}")
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError as exc:
        raise CleaningError(f"Lesefehler: {exc}") from exc


def write_text(path: str, text: str) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as exc:
        raise CleaningError(f"Schreibfehler: {exc}") from exc


def normalize_unicode(s: str) -> str:
    s = fix_text(s)
    # Orthographie-Glitches glätten
    s = s.replace("ſ", "s").replace("ﬅ", "st").replace("ﬁ", "fi").replace("ﬂ", "fl")
    return s


def strip_line_leading_numbers(s: str) -> Tuple[str, int]:
    pat = regex.compile(r"(?m)^[ \t]*\d{1,4}[ \t]+")
    new, n = pat.subn("", s)
    return new, n


def drop_page_furniture_lines(s: str) -> Tuple[str, int]:
    removed = 0
    out = []
    for ln in s.splitlines():
        if any(p.fullmatch(ln.strip()) for p in PAGE_FURNITURE_RES):
            removed += 1
            continue
        out.append(ln)
    return "\n".join(out), removed


def remove_inline_brackets(s: str) -> Tuple[str, int]:
    total = 0
    for rex in INLINE_BRACKETS_RE:
        s, n = rex.subn("", s)
        total += n
    return s, total


def remove_parenthetical_apparatus(s: str) -> Tuple[str, int]:
    total = 0
    for rex in PAREN_APPARAT_RE:
        s, n = rex.subn("", s)
        total += n
    return s, total


def remove_footnote_markers(s: str) -> Tuple[str, int]:
    total = 0
    for rex in FOOTNOTE_MARKERS_RE:
        s, n = rex.subn("", s)
        total += n
    return s, total


def dehyphenate_linebreaks(s: str) -> Tuple[str, int]:
    # Bindestrich + Zeilenumbruch vor kleinem Buchstaben
    pat = regex.compile(r"-\s*\n(?=[\p{Ll}äöüß])")
    new, n = pat.subn("", s)
    # weiche Trennzeichen
    new2, n2 = regex.subn(r"\u00AD", "", new)  # soft hyphen
    return new2, n + n2


def join_wrapped_lines(s: str) -> str:
    paras = regex.split(r"\n{2,}", s)
    joined = []
    for p in paras:
        p = p.strip()
        if not p:
            continue
        p = regex.sub(r"[ \t]*\n[ \t]*", " ", p)  # weiche Umbrüche -> Leerzeichen
        joined.append(p)
    return "\n\n".join(joined)


def normalize_whitespace(s: str) -> str:
    s = regex.sub(r"[ \t]{2,}", " ", s)
    s = regex.sub(r"\s+\n", "\n", s)
    s = regex.sub(r"\n{3,}", "\n\n", s)
    s = regex.sub(r"\s+([,.;:!?])", r"\1", s)
    s = regex.sub(r"\(\s+", "(", s)
    s = regex.sub(r"\s+\)", ")", s)
    return s.strip()


def drop_editorial_lines_by_terms(s: str) -> Tuple[str, int]:
    # harte Zeilen-Filter mit EDITORIAL_TOKENS
    removed = 0
    out = []
    for ln in s.splitlines():
        if EDITORIAL_RE.search(ln):
            removed += 1
            continue
        out.append(ln)
    return "\n".join(out), removed


def is_editorial_paragraph(p: str) -> bool:
    if EDITORIAL_RE.search(p):
        return True
    # sehr viele Klammern oder Klammern mit Seiten-/Zahlbezug
    if p.count("[") + p.count("]") > 0:
        return True
    if regex.search(r"\([^)]*\d{2,4}[^)]*\)", p):
        return True
    # Dichte von Semikolons/Kürzeln spricht für Apparat
    if p.count(";") >= 3:
        return True
    return False


def chronist_score(p: str) -> int:
    score = 0
    if START_TOKENS.search(p):
        score += 2
    if YEAR_PAT.search(p):
        score += 2
    if ARCHAIC_RE.search(p):
        score += 1
    # kleine Penalty, wenn ungewöhnlich viele Klammern vorhanden sind
    if p.count("(") + p.count(")") > 2:
        score -= 1
    return score


def filter_to_chronist(paragraphs: List[str], stats: Stats) -> List[str]:
    kept: List[str] = []
    for p in paragraphs:
        stats.paragraphs_total += 1
        if is_editorial_paragraph(p):
            stats.paragraphs_dropped_editorial += 1
            continue
        sc = chronist_score(p)
        if sc >= 2:
            kept.append(p.strip())
        else:
            stats.paragraphs_dropped_low_score += 1
    stats.paragraphs_kept = len(kept)
    return kept


def clean_text(raw: str) -> Tuple[str, Stats]:
    stats = Stats()
    stats.chars_in = len(raw)

    s = normalize_unicode(raw)

    # Zeilenweises Grobputzen
    s, _ = strip_line_leading_numbers(s)
    s, _ = drop_page_furniture_lines(s)
    s, _ = drop_editorial_lines_by_terms(s)

    # Inline-Reduktionen
    s, _ = remove_inline_brackets(s)
    s, _ = remove_parenthetical_apparatus(s)
    s, _ = remove_footnote_markers(s)

    # Zeilenumbruch-Themen
    s, _ = dehyphenate_linebreaks(s)

    # Absatzbildung
    s = join_wrapped_lines(s)

    # Paragraphen-Filter
    paragraphs = s.split("\n\n")
    kept = filter_to_chronist(paragraphs, stats)

    # Reflow + Feinschliff
    s = "\n\n".join(kept)
    s = normalize_whitespace(s)

    stats.chars_out = len(s)
    return s, stats


def main() -> None:
    print(f"[Debug] Lade Eingabe: {INPUT_PATH}")
    raw = read_text(INPUT_PATH)

    print("[Debug] Säubere und filtere auf Chronistentext …")
    cleaned, st = clean_text(raw)

    print(f"[Debug] Schreibe Ausgabe: {OUTPUT_PATH}")
    write_text(OUTPUT_PATH, cleaned)

    # Debug-Report
    print("\n[Debug] Bericht")
    print(f"  Absätze gesamt      : {st.paragraphs_total}")
    print(f"  behalten             : {st.paragraphs_kept}")
    print(f"  verworfen (editorial): {st.paragraphs_dropped_editorial}")
    print(f"  verworfen (lowscore) : {st.paragraphs_dropped_low_score}")
    print(f"  Zeichen: {st.chars_in} -> {st.chars_out} (Δ {st.chars_in - st.chars_out})")

    # Mini-Sanity-Check: ersten 500 Zeichen zeigen
    preview = cleaned[:500].replace("\n", " ⏎ ")
    print(f"\n[Debug] Vorschau: {preview}…")

    print("\n[Done] Nur-Chronist-Text gespeichert.")


if __name__ == "__main__":
    main()