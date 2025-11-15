"""
Peterlin-Korpusreiniger — Register und Scanspuren entfernen, Text normalisieren.

Dependencies:
  ftfy>=6.2.0
  regex>=2023.10.3

Ziel
----
- Entfernt Register-Seiten, Seiten-/Bibliotheksmöbel, Paginatormarker, ID/Signatur-Zeilen.
- Normalisiert Early-Modern-Glyphen: ſ→s, Ligaturen (ﬀ, ﬁ, ﬂ, ﬃ, ﬄ, ﬅ, ﬆ), Soft Hyphens.
- De-hyphenisiert über Zeilenumbrüche, reflowt Absätze, trimmt Leerräume.
- Optional orthographische Glättung: vnd/vnnd→und (konservativ, nur genaues Wort).

Heuristik-Hinweise
------------------
- „Regiſter“ kommt seitenweise; wir schneiden Blockweise vom Kopf „Regi[sſ]ter“ bis zum nächsten Seiten-Trenner „----- d / n -----“.
- Scanspuren: Bibliotheksstempel, Signaturen, Ganzzahl-/Römisch-Zeilen, Allcaps-Kopfzeilen.
- Early-Modern: langes s (ſ), typograph. Ligaturen, Soft Hyphen (U+00AD), u/v/i/j nicht gewaltsam modernisiert.

Defaults
--------
Input : ./Peterlin.txt
Output: ./output/peterlin_clean.txt

Usage
-----
1) Datei als ./Peterlin.txt bereitstellen.
2) Skript ausführen.
3) Ergebnis unter ./output/peterlin_clean.txt prüfen (Debug-Report am Ende).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Tuple

import regex  # type: ignore
from ftfy import fix_text


# --------------------------- Pfade & Optionen ---------------------------

INPUT_PATH = "./Peterlin.txt"
OUTPUT_DIR = "./output"
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "peterlin_clean.txt")

# Orthographie-Optionen
DO_MAP_VND_TO_UND = True  # 'vnd'/'vnnd' -> 'und' (Wortgrenzen, Kleinschreibung)


# --------------------------- Exceptions ---------------------------

class CleaningError(Exception):
    """Fatales Problem beim Säubern."""


# --------------------------- Muster ---------------------------

# Seiten-Trenner wie "----- 7 / 70 -----"
PAGE_BREAK_RE = regex.compile(r"^\s*-{2,}\s*\d+\s*/\s*\d+\s*-{2,}\s*$", flags=regex.MULTILINE)

# Register-Blöcke: vom Kopf "Regiſter/Regifter" bis zum nächsten Seiten-Trenner
REGISTER_BLOCK_RE = regex.compile(
    r"(?ms)^\s*Regi[sſ]ter\b.*?(?=^\s*-{2,}\s*\d+\s*/\s*\d+\s*-{2,}\s*$|\Z)"
)

# Bibliotheks-/Stempel-/Kopfleisten
STAMP_LINE_RES = [
    regex.compile(r"^\s*Bayer(ische|\.)(\s+Staatsbibliothek)?\b.*$", regex.IGNORECASE),
    regex.compile(r"^\s*BIBLIOTHECA\b.*MONACENSIS\b.*$", regex.IGNORECASE),
    regex.compile(r"^\s*Bayer\.\s*Staatsbibliothek\b.*$", regex.IGNORECASE),
    regex.compile(r"^\s*Oeco[nm]\.\b.*\d+\s*$", regex.IGNORECASE),  # "Oecon. 283"
]

# Reine IDs / Scancodes / nur Zahlen oder Winkelklammern-IDs
PURE_FURNITURE_LINE_RES = [
    regex.compile(r"^\s*\d+\s*$"),                             # reine Seiten-/Zahlzeile
    regex.compile(r"^\s*[IVXLCDM]{1,8}\s*$"),                  # römische Zahl allein
    regex.compile(r"^\s*[A-ZÄÖÜ]\s*$"),                        # Einzelbuchstabe (B, C, ...)
    regex.compile(r"^\s*A\s*[IVXLCDM]+(\s*\([^)]+\))?\s*$"),   # "A XIX" (mit evtl. Klammer)
    regex.compile(r"^\s*<\d{8,}>\s*$"),                        # "<36630565100015>"
    regex.compile(r"^\s*-{2,}\s*$"),                           # nur Trennstrich
    regex.compile(r"^\s*[A-ZÄÖÜ][A-ZÄÖÜ\s\.\-]{2,40}$"),       # kurze ALLCAPS-Köpfe
]

# Zeilen wie "Getruckt zu Augspurg ..." optional entfernen? -> behalten (Quellenende)
REMOVE_PRINTER_COLOPHON = False


# --------------------------- Hilfen ---------------------------

@dataclass
class Stats:
    chars_in: int = 0
    chars_out: int = 0
    lines_in: int = 0
    lines_out: int = 0
    register_blocks_removed: int = 0
    page_breaks_removed: int = 0
    furniture_lines_removed: int = 0
    stamp_lines_removed: int = 0
    dehyphenations: int = 0


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
    # Ligaturen & langes s
    s = (
        s.replace("ſ", "s")
        .replace("ﬀ", "ff")
        .replace("ﬁ", "fi")
        .replace("ﬂ", "fl")
        .replace("ﬃ", "ffi")
        .replace("ﬄ", "ffl")
        .replace("ﬅ", "st")
        .replace("ﬆ", "st")
        .replace("\u00AD", "")  # Soft Hyphen
    )
    return s


def remove_register_blocks(s: str, stats: Stats) -> str:
    while True:
        m = REGISTER_BLOCK_RE.search(s)
        if not m:
            break
        s = s[: m.start()] + s[m.end() :]
        stats.register_blocks_removed += 1
    return s


def remove_page_breaks(s: str, stats: Stats) -> str:
    s, n = PAGE_BREAK_RE.subn("", s)
    stats.page_breaks_removed += n
    return s


def remove_stamp_lines(s: str, stats: Stats) -> str:
    out_lines: List[str] = []
    removed = 0
    for ln in s.splitlines():
        if any(p.search(ln) for p in STAMP_LINE_RES):
            removed += 1
            continue
        out_lines.append(ln)
    stats.stamp_lines_removed += removed
    return "\n".join(out_lines)


def remove_furniture_lines(s: str, stats: Stats) -> str:
    out_lines: List[str] = []
    removed = 0
    for ln in s.splitlines():
        if any(p.fullmatch(ln.strip()) for p in PURE_FURNITURE_LINE_RES):
            removed += 1
            continue
        out_lines.append(ln)
    stats.furniture_lines_removed += removed
    return "\n".join(out_lines)


def strip_line_leading_numbers(s: str) -> Tuple[str, int]:
    # Entfernt Zeilenanfangs-Ziffernblöcke samt nachfolgendem Leerraum
    pat = regex.compile(r"(?m)^[ \t]*\d{1,4}[ \t]+")
    new_s, n = pat.subn("", s)
    return new_s, n


def dehyphenate_linebreaks(s: str, stats: Stats) -> str:
    # „ver-\nfasst“ -> „verfasst“ (nur wenn nächstes Zeichen Kleinbuchstabe)
    pat = regex.compile(r"-\s*\n(?=[\p{Ll}äöüß])")
    new_s, n = pat.subn("", s)
    stats.dehyphenations += n
    return new_s


def join_wrapped_lines(s: str) -> str:
    # Absätze durch Leerzeilen getrennt; innerhalb auf eine Zeile zusammenführen
    paras = regex.split(r"\n{2,}", s)
    out: List[str] = []
    for p in paras:
        p = p.strip()
        if not p:
            continue
        p = regex.sub(r"[ \t]*\n[ \t]*", " ", p)
        out.append(p)
    return "\n\n".join(out)


def normalize_whitespace(s: str) -> str:
    s = regex.sub(r"[ \t]{2,}", " ", s)
    s = regex.sub(r"\s+\n", "\n", s)
    s = regex.sub(r"\n{3,}", "\n\n", s)
    s = regex.sub(r"\s+([,.;:!?])", r"\1", s)
    s = regex.sub(r"\(\s+", "(", s)
    s = regex.sub(r"\s+\)", ")", s)
    return s.strip()


def map_vnd_to_und(s: str) -> str:
    if not DO_MAP_VND_TO_UND:
        return s
    # Nur exakte Wörter „vnd“, „vnnd“, „Vnd“, „Vnnd“ an Wortgrenzen
    return regex.sub(r"\b[vV]n{1,2}d\b", "und", s)


def maybe_remove_printer_colophon(s: str) -> str:
    if not REMOVE_PRINTER_COLOPHON:
        return s
    return regex.sub(r"(?ms)^\s*Getruckt\s+zu\s+Augspurg.*?\Z", "", s)


# --------------------------- Pipeline ---------------------------

def clean_text(raw: str) -> Tuple[str, Stats]:
    st = Stats()
    st.chars_in = len(raw)
    st.lines_in = raw.count("\n") + 1

    s = normalize_unicode(raw)

    # Block- und Zeilenmöbel entfernen
    s = remove_register_blocks(s, st)
    s = remove_page_breaks(s, st)
    s = remove_stamp_lines(s, st)
    s = remove_furniture_lines(s, st)

    # Zeilenanfangs-Ziffern (z. B. "283  ") kappen
    s, _ = strip_line_leading_numbers(s)

    # De-hyphenisieren und reflowen
    s = dehyphenate_linebreaks(s, st)
    s = join_wrapped_lines(s)

    # Optionale Orthographie-Glättung
    s = map_vnd_to_und(s)

    # Whitespace säubern
    s = normalize_whitespace(s)

    # Optional: Druckvermerk am Schluss entfernen
    s = maybe_remove_printer_colophon(s)

    st.chars_out = len(s)
    st.lines_out = s.count("\n") + 1
    return s, st


# --------------------------- main ---------------------------

def main() -> None:
    print(f"[Debug] Lade Eingabe: {INPUT_PATH}")
    raw = read_text(INPUT_PATH)

    print("[Debug] Säubere Register/Seitenmöbel und normalisiere Zeichen …")
    cleaned, st = clean_text(raw)

    print(f"[Debug] Schreibe Ausgabe: {OUTPUT_PATH}")
    write_text(OUTPUT_PATH, cleaned)

    # Kurzbericht
    print("\n[Debug] Bericht")
    print(f"  Zeilen: {st.lines_in} -> {st.lines_out}")
    print(f"  Zeichen: {st.chars_in} -> {st.chars_out} (Δ {st.chars_in - st.chars_out})")
    print(f"  Register-Blöcke entfernt : {st.register_blocks_removed}")
    print(f"  Seiten-Trenner entfernt  : {st.page_breaks_removed}")
    print(f"  Stempelzeilen entfernt   : {st.stamp_lines_removed}")
    print(f"  Möbelzeilen entfernt     : {st.furniture_lines_removed}")
    print(f"  Dehyphenisierungen       : {st.dehyphenations}")

    preview = cleaned[:600].replace("\n", " ⏎ ")
    print(f"\n[Debug] Vorschau: {preview}…")
    print("\n[Done] Normalisierter Text gespeichert.")


if __name__ == "__main__":
    main()