# /Users/programming/PycharmProjects/Find_Bibliography_NEw/check_pdfs_and_zotero.py
"""
Prüft PDFs im AZK-Verzeichnis und (optional) gleicht sie mit Zotero ab.

Teil 1 (immer):
    - Läuft rekursiv über PDF_BASE_DIR
    - Prüft pro PDF mit PyPDF2, ob ein Text-Layer vorhanden ist
      (also ob die Datei grundsätzlich indizierbar ist)
    - Schreibt einen Report als JSON

Teil 2 (optional, DO_ZOTERO_API = True):
    - Verwendet Zotero-Web-API (pyzotero) mit Zugangsdaten aus .env
    - Lädt alle Attachment-Items aus deiner Library
    - Vergleicht Dateinamen mit den PDFs im AZK-Verzeichnis
    - Markiert pro PDF:
        * found_in_zotero: True/False
        * zotero_items: Liste von {key, title, linkMode}

WICHTIG:
    - Das Script kann den lokalen Volltextindex von Zotero NICHT steuern.
      Es prüft nur, ob deine PDFs technisch textbasiert sind und
      ob sie in der Zotero-Library auftauchen.
    - Keine Konsolen-Eingaben; Konfiguration über Variablen unten.

Dependencies:
    pip install PyPDF2 pyzotero python-dotenv
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Dict, Any, Optional

from PyPDF2 import PdfReader
from dotenv import load_dotenv

# Zotero (optional)
try:
    from pyzotero import zotero as pyzotero
    HAVE_PYZOTERO = True
except Exception:
    HAVE_PYZOTERO = False
DO_ZOTERO_API = True

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path("/Users/programming/PycharmProjects/Find_Bibliography_NEw")

# Dein AZK-Verzeichnis mit PDFs
PDF_BASE_DIR = PROJECT_ROOT / "data" / "azk_library"

# Report-Datei
REPORT_JSON = PROJECT_ROOT / "azk_pdf_report.json"

# Zweite Stufe: Zotero-API benutzen?
#   False  -> Script macht NUR lokale PDF-Prüfung (sicher).
#   True   -> Zusätzlich per Web-API nach Zotero-Items mit passenden Dateinamen suchen.
DO_ZOTERO_API = True

# .env laden (für ZOTERO_* Variablen)
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY", "").strip()
ZOTERO_LIBRARY_ID = os.getenv("ZOTERO_LIBRARY_ID", "").strip()
ZOTERO_LIBRARY_TYPE = os.getenv("ZOTERO_LIBRARY_TYPE", "user").strip() or "user"


# ---------------------------------------------------------------------------
# Datenstrukturen
# ---------------------------------------------------------------------------

@dataclass
class PdfInfo:
    rel_path: str
    abs_path: str
    has_text: bool
    text_sample: str
    error: Optional[str] = None
    # Zotero-bezogene Felder (werden erst in Stufe 2 gefüllt)
    zotero_found: bool = False
    zotero_items: Optional[List[Dict[str, Any]]] = None


# ---------------------------------------------------------------------------
# PDF-Analyse
# ---------------------------------------------------------------------------

def analyze_pdf(path: Path, base_dir: Path) -> PdfInfo:
    rel = str(path.relative_to(base_dir))
    abs_str = str(path)

    try:
        reader = PdfReader(str(path))
        text_chunks = []
        # nur die ersten paar Seiten prüfen, das reicht als Heuristik
        max_pages = min(5, len(reader.pages))
        for i in range(max_pages):
            page = reader.pages[i]
            txt = page.extract_text() or ""
            if txt:
                text_chunks.append(txt.strip())

        full_text = "\n".join(text_chunks).strip()
        has_text = len(full_text) > 40  # einfache Schwelle
        sample = full_text[:400] if full_text else ""

        return PdfInfo(
            rel_path=rel,
            abs_path=abs_str,
            has_text=has_text,
            text_sample=sample,
            error=None,
        )
    except Exception as e:
        return PdfInfo(
            rel_path=rel,
            abs_path=abs_str,
            has_text=False,
            text_sample="",
            error=str(e),
        )


def scan_pdf_directory(base_dir: Path) -> List[PdfInfo]:
    if not base_dir.exists():
        raise FileNotFoundError(f"PDF_BASE_DIR nicht gefunden: {base_dir}")

    results: List[PdfInfo] = []
    for path in base_dir.rglob("*.pdf"):
        info = analyze_pdf(path, base_dir)
        results.append(info)
    return results


# ---------------------------------------------------------------------------
# Zotero-Abgleich (optional)
# ---------------------------------------------------------------------------

class ZoteroClientWrapper:
    def __init__(self):
        self.enabled = (
            DO_ZOTERO_API
            and HAVE_PYZOTERO
            and bool(ZOTERO_API_KEY)
            and bool(ZOTERO_LIBRARY_ID)
        )
        self.client = None
        if self.enabled:
            try:
                self.client = pyzotero.Zotero(
                    ZOTERO_LIBRARY_ID,
                    ZOTERO_LIBRARY_TYPE,
                    ZOTERO_API_KEY,
                )
            except Exception as e:
                print(f"[WARN] Zotero-Initialisierung fehlgeschlagen: {e}")
                self.enabled = False

    def is_enabled(self) -> bool:
        return self.enabled

    def build_attachment_index(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Baut ein Mapping:
            filename_lower -> Liste von Items {key, title, linkMode}
        für alle Attachment-Items in der Library.
        """
        if not self.enabled or not self.client:
            return {}

        print("[INFO] Lese Attachment-Items aus Zotero ...")
        try:
            attachments = self.client.everything(
                self.client.items(itemType="attachment")
            )
        except Exception as e:
            print(f"[WARN] Zotero-Items konnten nicht geladen werden: {e}")
            return {}

        index: Dict[str, List[Dict[str, Any]]] = {}
        for item in attachments:
            data = item.get("data", {})
            key = data.get("key")
            title = data.get("title", "")
            link_mode = data.get("linkMode", "")
            filename = data.get("filename", "")

            # Fallback: manche Attachments haben keinen filename, nur title
            name = filename or title
            name = (name or "").strip()
            if not name:
                continue

            name_lower = name.lower()
            entry = {
                "key": key,
                "title": title,
                "linkMode": link_mode,
                "filename": filename,
            }
            index.setdefault(name_lower, []).append(entry)

        print(f"[INFO] {len(index)} verschiedene Dateinamen in Zotero erfasst.")
        return index


def match_pdfs_with_zotero(pdfs: List[PdfInfo]) -> None:
    zot = ZoteroClientWrapper()
    if not zot.is_enabled():
        print("[INFO] Zotero-API ist deaktiviert oder nicht konfiguriert. "
              "Setze DO_ZOTERO_API = True und ZOTERO_* in .env, um den Abgleich zu aktivieren.")
        return

    index = zot.build_attachment_index()
    if not index:
        return

    for info in pdfs:
        filename_lower = Path(info.abs_path).name.lower()
        if filename_lower in index:
            info.zotero_found = True
            info.zotero_items = index[filename_lower]
        else:
            info.zotero_found = False
            info.zotero_items = []


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"[INFO] Scanne PDFs unter: {PDF_BASE_DIR}")
    pdf_infos = scan_pdf_directory(PDF_BASE_DIR)
    print(f"[INFO] {len(pdf_infos)} PDF-Dateien gefunden.")

    # Optionaler Zotero-Abgleich
    match_pdfs_with_zotero(pdf_infos)

    # Report schreiben
    report_data = [asdict(p) for p in pdf_infos]
    REPORT_JSON.write_text(
        json.dumps(report_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[OK] Report geschrieben nach: {REPORT_JSON}")

    # Kurze Zusammenfassung
    no_text = [p for p in pdf_infos if not p.has_text and not p.error]
    errors = [p for p in pdf_infos if p.error]
    print(f"[INFO] PDFs ohne erkennbaren Text-Layer: {len(no_text)}")
    print(f"[INFO] PDFs mit Fehler beim Lesen: {len(errors)}")


if __name__ == "__main__":
    main()