# /Users/programming/PycharmProjects/BA-Codes/build_infoclio_docx.py
"""
Erzeugt aus citations_template.json ein Markdown mit Pandoc-Zitaten und optional
ein DOCX mit InfoClio-Fussnoten (über pandoc + CSL).

Voraussetzungen:
    - citations_template.json unter mark_quotable/data/
    - Zotero-Export als CSL-JSON: mark_quotable/data/ba_azk_csl.json
    - InfoClio-CSL: mark_quotable/data/infoclio.csl
    - pandoc im PATH, falls RUN_PANDOC = True

Nutzung:
    1. python build_infoclio_docx.py
    2. Es entsteht:
         - mark_quotable/data/ba_azk_infoclio.md
         - (optional) mark_quotable/data/ba_azk_infoclio.docx
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path("/Users/programming/PycharmProjects/BA-Codes")
DATA_DIR = PROJECT_ROOT / "mark_quotable" / "data"

CITATIONS_TEMPLATE_PATH = DATA_DIR / "citations_template.json"
BIBLIOGRAPHY_JSON = DATA_DIR / "ba_azk_csl.json"
CSL_STYLE = DATA_DIR / "infoclio.csl"

OUTPUT_MD = DATA_DIR / "ba_azk_infoclio.md"
OUTPUT_DOCX = DATA_DIR / "ba_azk_infoclio.docx"

# Wenn True: Script ruft pandoc direkt auf und erzeugt das DOCX
RUN_PANDOC = False
PANDOC_PATH = "pandoc"

DOC_TITLE = "BA – Alter Zürichkrieg"


# ---------------------------------------------------------------------------
# Datenmodell
# ---------------------------------------------------------------------------


class CitationEntry:
    """
    Eine Zeile aus citations_template.json

    Erwartete Felder im JSON:
      paragraph_index: int
      sentence_index: int
      sentence_text: str
      footnote: str         # hier: Seiten / Zusatz (z.B. "35–36", "vgl. 35–36")
      zotero_key: str       # Zotero-Item-Key, z.B. "ABCD1234"
    """

    def __init__(self, data: Dict[str, Any]):
        self.paragraph_index: int = int(data.get("paragraph_index", 0))
        self.sentence_index: int = int(data.get("sentence_index", 0))
        self.sentence_text: str = str(data.get("sentence_text", ""))
        self.footnote: str = str(data.get("footnote", ""))
        self.zotero_key: str = str(data.get("zotero_key", "")).strip()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "paragraph_index": self.paragraph_index,
            "sentence_index": self.sentence_index,
            "sentence_text": self.sentence_text,
            "footnote": self.footnote,
            "zotero_key": self.zotero_key,
        }


def load_citations(path: Path) -> List[CitationEntry]:
    if not path.exists():
        raise FileNotFoundError(f"Template nicht gefunden: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("citations_template.json muss eine Liste von Objekten enthalten.")
    return [CitationEntry(item) for item in data]


# ---------------------------------------------------------------------------
# Hilfsfunktionen: Footnote → Pandoc-Citation
# ---------------------------------------------------------------------------


def build_citation_markup(zotero_key: str, footnote_text: str) -> str:
    """
    Baut aus zotero_key und footnote_text eine Pandoc-Zitiermarkierung.

    Beispiele (Input → Output):

    zotero_key="ABCD1234", footnote_text=""
        → " [@ABCD1234]"

    zotero_key="ABCD1234", footnote_text="35–36"
        → " [@ABCD1234, pp. 35–36]"

    zotero_key="ABCD1234", footnote_text="vgl. 35–36"
        → " [vgl. @ABCD1234, pp. 35–36]"

    Vorgehen:
      - Alles vor der ersten Ziffer = Prefix (z.B. "vgl.")
      - Alles ab erster Ziffer = Seiten (Locator)
      - Seiten werden als "pp. X" an den Citation angehängt, damit CSL/InfoClio
        eine Seitenangabe formatieren kann.
    """
    key = zotero_key.strip()
    if not key:
        return ""

    text = (footnote_text or "").strip()
    if not text:
        return f" [@{key}]"

    # Suche erste Ziffer (Start der Seitenangabe)
    m = re.search(r"\d", text)
    if not m:
        # Nur Prefix, keine Seiten
        prefix = text.strip()
        return f" [{prefix} @{key}]"

    prefix_raw = text[: m.start()]
    locator_raw = text[m.start() :]

    prefix = prefix_raw.strip(" ,;:")
    locator = locator_raw.strip(" ,;:")

    # Locator als Seitenangabe mit "pp." markieren
    locator_part = f"pp. {locator}" if locator else ""

    if prefix and locator_part:
        return f" [{prefix} @{key}, {locator_part}]"
    elif locator_part:
        return f" [@{key}, {locator_part}]"
    else:
        return f" [@{key}]"


def build_paragraphs(entries: List[CitationEntry]) -> List[str]:
    """
    Baut aus den Einträgen fortlaufende Absätze, nach paragraph_index gruppiert.
    Pro Satz wird – falls zotero_key vorhanden – eine Pandoc-Zitierung angehängt.
    """
    # Nach Absatz + Satz sortieren
    entries_sorted = sorted(
        entries, key=lambda e: (e.paragraph_index, e.sentence_index)
    )

    paragraphs: List[str] = []
    current_para_index: Optional[int] = None
    current_sentences: List[str] = []

    for entry in entries_sorted:
        if current_para_index is None:
            current_para_index = entry.paragraph_index

        if entry.paragraph_index != current_para_index:
            if current_sentences:
                paragraphs.append(" ".join(current_sentences))
            current_sentences = []
            current_para_index = entry.paragraph_index

        sentence = entry.sentence_text.strip()
        if not sentence:
            continue

        citation = build_citation_markup(entry.zotero_key, entry.footnote)
        current_sentences.append(f"{sentence}{citation}")

    if current_sentences:
        paragraphs.append(" ".join(current_sentences))

    return paragraphs


def build_markdown(entries: List[CitationEntry]) -> str:
    """
    Erzeugt den vollständigen Markdown-Text mit YAML-Header für pandoc.
    """
    # Relativnamen, da OUTPUT_MD im gleichen Ordner wie CSL/Biblio liegt
    biblio_name = BIBLIOGRAPHY_JSON.name
    csl_name = CSL_STYLE.name

    paragraphs = build_paragraphs(entries)

    header_lines = [
        "---",
        f'title: "{DOC_TITLE}"',
        f"bibliography: {biblio_name}",
        f"csl: {csl_name}",
        "link-citations: true",
        "---",
        "",
    ]

    body_lines: List[str] = []
    for p in paragraphs:
        body_lines.append(p)
        body_lines.append("")  # Leerzeile zwischen Absätzen

    return "\n".join(header_lines + body_lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# pandoc-Aufruf
# ---------------------------------------------------------------------------


def run_pandoc(md_path: Path, docx_path: Path) -> None:
    """
    Ruft pandoc auf, um aus dem Markdown ein DOCX mit Fussnoten zu erzeugen.
    Bibliographie und CSL werden aus dem YAML-Header gelesen.
    """
    cmd = [
        PANDOC_PATH,
        str(md_path),
        "--citeproc",
        "-o",
        str(docx_path),
    ]
    print("[INFO] Starte pandoc:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    print(f"[OK] DOCX erzeugt: {docx_path}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    print(f"[INFO] Lade Zitations-Template: {CITATIONS_TEMPLATE_PATH}")
    entries = load_citations(CITATIONS_TEMPLATE_PATH)
    print(f"[INFO] {len(entries)} Einträge geladen.")

    if not BIBLIOGRAPHY_JSON.exists():
        print(f"[WARN] Bibliography-JSON fehlt: {BIBLIOGRAPHY_JSON}")
        print("       Bitte Zotero-CSL-JSON exportieren (BA-AZK) und dort speichern.")
    if not CSL_STYLE.exists():
        print(f"[WARN] CSL-Style fehlt: {CSL_STYLE}")
        print("       Bitte InfoClio-CSL unter diesem Namen ablegen.")

    print("[INFO] Erzeuge Markdown mit Pandoc-Zitaten …")
    md_text = build_markdown(entries)

    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_MD.open("w", encoding="utf-8") as f:
        f.write(md_text)

    print(f"[OK] Markdown geschrieben nach: {OUTPUT_MD}")

    if RUN_PANDOC:
        if not BIBLIOGRAPHY_JSON.exists() or not CSL_STYLE.exists():
            print("[ERROR] Bibliography-JSON oder CSL-Style fehlen, pandoc wird nicht aufgerufen.")
            return
        try:
            run_pandoc(OUTPUT_MD, OUTPUT_DOCX)
        except Exception as e:
            print("[ERROR] pandoc-Aufruf fehlgeschlagen:", e)
    else:
        print("[INFO] RUN_PANDOC = False – kein DOCX erzeugt.")
        print("      DOCX selbst bauen mit z.B.:")
        print(f"      pandoc '{OUTPUT_MD.name}' --citeproc -o '{OUTPUT_DOCX.name}'")


if __name__ == "__main__":
    main()