# ./translate_mhd.py
# Einfache heuristische Normalisierung von (mittel-/frühneuhochdeutschem) Text
# in modernes Deutsch, z.B. für chr_Peterlin.txt -> chr_Peterlin_modern.txt.
# Dependencies: Python 3.9+ (nur Standardbibliothek)

from __future__ import annotations

import re
from pathlib import Path


# -----------------------------------------------------------
# Konfiguration
# -----------------------------------------------------------

INPUT_PATH = Path("chr_Peterlin.txt")          # Originaltext
OUTPUT_PATH = Path("chr_Peterlin_modern.txt")  # Ausgabe mit moderner Schreibweise


# -----------------------------------------------------------
# Grundlegende Normalisierung
# -----------------------------------------------------------

def normalize_characters(text: str) -> str:
    """
    Normalisiert Sonderzeichen und typische alte Varianten grob.
    Diese Stufe verändert bewusst keine Wortbedeutungen, sondern nur
    Zeichen und häufige Grapheme.
    """
    replacements = {
        "ſ": "s",      # langes s
        "Æ": "Ae",
        "æ": "ae",
        "Œ": "Oe",
        "œ": "oe",
        "å": "a",
        "ů": "u",
        "ÿ": "y",
        "ẽ": "en",
        "õ": "on",
        "ã": "an",
        "ꝛ": "r",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    # Häufige Mehrbuchstaben-Schreibungen
    multi = [
        (r"\bauff\b", "auf"),
        (r"\bauffs\b", "aufs"),
        (r"\bvnnd\b", "und"),
        (r"\bvnd\b", "und"),
        (r"\bvn\b", "und"),
        (r"\bvber\b", "über"),
        (r"\bubir\b", "über"),
        (r"\beygentlich\b", "eigentlich"),
        (r"\bſo\b", "so"),   # nach ſ→s wird das nur zur Sicherheit nochmal gefasst
    ]
    for pattern, repl in multi:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)

    return text


# -----------------------------------------------------------
# Wortliste: alte Formen → moderne Grundform
# -----------------------------------------------------------

WORD_MAP: dict[str, str] = {
    # sehr häufig
    "und": "und",            # bleibt, aber wichtig für Korrektheit der Ersetzung
    "das": "das",
    "was": "was",
    "als": "als",
    "noch": "noch",

    # Rechtschreibung / kleine Modernisierung
    "speis": "speise",
    "speys": "speise",
    "speyse": "speise",
    "speisen": "speisen",
    "flaysch": "fleisch",
    "flaisch": "fleisch",
    "flaiſch": "fleisch",
    "kalbsflaysch": "kalbsfleisch",
    "kalbflaisch": "kalbfleisch",
    "huner": "hühner",
    "huner": "hühner",
    "huner": "hühner",
    "huner": "hühner",
    "huner": "hühner",
    "huner": "hühner",
    "hunern": "hühnern",
    "vogel": "vögel",
    "vogel": "vögel",
    "vygel": "vögel",
    "weinpfeffer": "weinpfeffer",

    # Flüssiges
    "weyn": "wein",
    "wein": "wein",
    "weynber": "weinberg",
    "weynber": "weinberg",
    "most": "most",
    "essig": "essig",
    "effig": "essig",
    "byer": "bier",

    # häufige Funktionswörter / Formen
    "mag": "kann",
    "magst": "kannst",
    "sol": "soll",
    "solt": "sollst",
    "soltu": "sollst du",
    "soltu": "sollst du",
    "soltst": "solltest",
    "sey": "sei",
    "seyn": "sein",
    "seind": "sind",
    "seynd": "sind",
    "seyt": "seid",
    "nicht": "nicht",
    "nit": "nicht",
    "allso": "also",
    "alsdann": "dann",
    "darnach": "danach",
    "vorhin": "zuvor",
    "hernach": "danach",

    # Küche / Alltag
    "opffel": "äpfel",
    "apffel": "äpfel",
    "apffeln": "äpfeln",
    "byrn": "birnen",
    "birn": "birnen",
    "praten": "braten",
    "gepraten": "gebraten",
    "gebackens": "gebäck",
    "küchlin": "küchlein",
    "kuchlin": "küchlein",
    "kuchlein": "küchlein",
    "gebachens": "gebäck",
    "tayg": "teig",
    "turtte": "torte",
    "turten": "torten",
    "galrath": "galretter",
    "supp": "suppe",
    "suppenn": "suppen",
    "muss": "mus",
    "mueß": "mus",
    "müß": "mus",

    # Maße / Zubereitung
    "sieden": "kochen",
    "gesoten": "gekocht",
    "gesotten": "gekocht",
    "gesud": "gekocht",
    "seuden": "kochen",
    "prenn": "brennen",
    "praun": "braun",
    "gail": "ganz",
    "lauter": "klar",
    "geyst": "geist",

    # Medizin / Weinabschnitt
    "tugent": "tugend",
    "argney": "arznei",
    "artzney": "arznei",
    "naturlich": "natürlich",
    "fiechtag": "krankheit",
    "siechtag": "krankheit",
    "franckheit": "krankheit",
    "magen": "magen",
    "leber": "leber",
    "glider": "glieder",
    "complexion": "konstitution",
}

# -----------------------------------------------------------
# Hilfslogik zur Wortübersetzung mit Beibehaltung der Groß-/Kleinschreibung
# -----------------------------------------------------------

WORD_RE = re.compile(r"\w+", flags=re.UNICODE)


def _apply_casing(original: str, modern: str) -> str:
    """
    Passt die Groß-/Kleinschreibung des übersetzten Wortes
    an das Original an.
    """
    if original.isupper():
        return modern.upper()
    if original.istitle():
        return modern.capitalize()
    # ansonsten Kleinschreibung übernehmen
    return modern


def translate_tokens(text: str, word_map: dict[str, str]) -> str:
    """
    Wendet WORD_MAP wortweise an, ohne Interpunktion oder
    Abstände zu zerstören.
    """

    def _replace(match: re.Match) -> str:
        word = match.group(0)
        lower = word.lower()
        modern = word_map.get(lower)
        if not modern:
            return word
        return _apply_casing(word, modern)

    return WORD_RE.sub(_replace, text)


# -----------------------------------------------------------
# Pipeline
# -----------------------------------------------------------

def modernize(text: str) -> str:
    """
    Komplettpipeline: Zeichen normalisieren, dann Wortersetzung.
    """
    normalized = normalize_characters(text)
    translated = translate_tokens(normalized, WORD_MAP)
    return translated


def main() -> None:
    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"Eingabedatei nicht gefunden: {INPUT_PATH.resolve()}"
        )

    original = INPUT_PATH.read_text(encoding="utf-8", errors="ignore")
    modern = modernize(original)
    OUTPUT_PATH.write_text(modern, encoding="utf-8")

    print(f"Fertig. Ausgabe geschrieben nach: {OUTPUT_PATH.resolve()}")


if __name__ == "__main__":
    main()