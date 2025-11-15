# organizer/chronik/chronik_finder/mhd_utils.py
#!/usr/bin/env python3
"""
Hilfsfunktionen für robuste Suche in mittelhochdeutschen / frühneuzeitlichen Texten.

Zwei Ebenen:
1) Text-Normalisierung (normalize_mhd_text):
   - reduziert OCR-/Schreibvarianten auf einen stabileren „Such-Alphabet“-Kern.
   - mappet z. B. ſ → s, u/v → u, i/j/y → i, ae/ä → ae, etc.

2) Pattern-Erweiterung für Namen (to_mhd_regex_literal):
   - nimmt LITERALS (keine fertigen Regex) wie "Hans Fründ"
   - erzeugt Regex, die typische Varianten erlauben:
       u/v, i/j/y, ſ/s, k/c bei "ck", etc.
   - vorhandene Regex-Metazeichen werden escaped.

Typischer Einsatz:
   - beim Aufbau von chroniken_canon: aus Canon-Namen zusätzliche
     tolerant Patterns generieren.
   - im Text-Extractor: normalize_mhd_text() vor dem Regex-Match anwenden,
     damit die Patterns nicht alle Varianten explizit auffangen müssen.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict


# ---------------------- Grund-Normalisierung ----------------------


def _strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def normalize_mhd_text(text: str) -> str:
    """
    Normalisiert OCR-/Alt-Schreibung in einen stabileren Suchraum.

    Ideen:
      - Unicode-Normalisierung + Diakritika weg
      - langes s (ſ) → s
      - u/v → u
      - i/j/y → i
      - ß → ss
      - vereinheitlichte Spacing

    Du kannst diese Funktion z. B. direkt in text.extract_text() am Ende
    aufrufen, bevor die Regex-Matches laufen.
    """
    if not text:
        return ""

    # Unicode & Akzente entfernen
    text = _strip_accents(text)

    # Lang-s
    text = text.replace("ſ", "s")

    # ß → ss
    text = text.replace("ß", "ss")

    # u/v ↦ u
    text = text.replace("V", "U").replace("v", "u")

    # i/j/y ↦ i
    text = text.replace("J", "I").replace("j", "i")
    text = text.replace("Y", "I").replace("y", "i")

    # vereinfachte Umlaute (nur falls OCR sie nicht eh schon zerschossen hat)
    # (du kannst das auch weglassen, wenn deine Patterns schon [äa] etc. nutzen)
    text = text.replace("Ä", "Ae").replace("ä", "ae")
    text = text.replace("Ö", "Oe").replace("ö", "oe")
    text = text.replace("Ü", "Ue").replace("ü", "ue")

    # Standardisieren
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ---------------------- Pattern-Erweiterung ----------------------


# Char-Äquivalenzen für Regex-Klassen
CHAR_EQUIV: Dict[str, str] = {
    # s / lang-s
    "s": "sſ",
    "S": "Ssſ",

    # u / v
    "u": "uv",
    "U": "UV",
    "v": "uv",
    "V": "UV",

    # i / j / y
    "i": "ijy",
    "I": "IJY",
    "j": "ijy",
    "J": "IJY",
    "y": "ijy",
    "Y": "IJY",

    # grob: k/c in bestimmten Kontexten, nur Kleinbuchstaben
    # (Großbuchstaben meist Eigennamen, oft stabiler)
    "k": "kc",
    # Umlaute kannst du, wenn gewünscht, auch variabler machen:
    # "ä": "aäae",
    # "ö": "oöoe",
    # "ü": "uüue",
}


def to_mhd_regex_literal(literal: str) -> str:
    """
    Wandelt einen PLAIN-Text (kein fertiges Regex!) in ein Regex um,
    das typische mittelhochdeutsche Varianten zulässt.

    Regeln (char-basiert, außerhalb von [...] und Escapes):
      - s → [sſ]
      - u/v → [uv]
      - i/j/y → [ijy]
      - k → [kc]
      - Umlaute kannst du bei Bedarf in CHAR_EQUIV ergänzen.

    Regex-Metazeichen (. ^ $ * + ? { } [ ] \ | ( )) werden geescaped,
    damit du wirklich von einem „Literalnamen“ ausgehst.
    """
    out: list[str] = []
    for ch in literal:
        # Metazeichen immer escapen
        if ch in r".^$*+?{}[]\|()":
            out.append("\\" + ch)
            continue

        eq = CHAR_EQUIV.get(ch)
        if eq:
            # Duplikate entfernen, stabil sortieren
            chars = "".join(sorted(set(eq)))
            out.append(f"[{chars}]")
        else:
            # “normales” Zeichen wörtlich
            out.append(re.escape(ch))

    return "".join(out)


# ---------------------- Komfort-Funktionen ----------------------


def wrap_word_boundary(pattern: str) -> str:
    """
    Ergänzt ein Pattern um Wortgrenzen (\b), falls du explizit Namen
    matchen willst und nicht innerhalb längerer Wörter landen möchtest.
    """
    return rf"\b{pattern}\b"


def make_mhd_name_patterns(modern_name: str) -> str:
    """
    Komfort: aus einem modernen Namen wie "Hans Fründ"
    direkt ein brauchbares Regex bauen:

        modern_name = "Hans Fründ"
        -> \\bh[ai]ns\\s+fr[üu]nd ... (vereinfacht)

    Konkrete Ausgabe hängt von CHAR_EQUIV ab.
    """
    core = to_mhd_regex_literal(modern_name)
    # grob: mehrere Leerzeichen → \s+
    core = re.sub(r"\\\s+", r"\\s+", core)
    return wrap_word_boundary(core)