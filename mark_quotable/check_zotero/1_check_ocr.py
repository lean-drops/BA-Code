# /Users/programming/PycharmProjects/BA-Codes/summarize_azk_report.py
"""
Liest azk_pdf_report.json und gibt kompakte Übersichten aus:

- PDFs ohne Text-Layer (OCR-Kandidaten)
- PDFs mit Text, aber nicht in Zotero gefunden
- PDFs mit Text und in Zotero gefunden
"""

from pathlib import Path
import json

PROJECT_ROOT = Path("/Users/programming/PycharmProjects/BA-Codes")
REPORT_JSON = PROJECT_ROOT / "azk_pdf_report.json"


def main():
    if not REPORT_JSON.exists():
        raise FileNotFoundError(f"{REPORT_JSON} nicht gefunden – zuerst search_footnotes.py ausführen.")

    with REPORT_JSON.open("r", encoding="utf-8") as f:
        entries = json.load(f)

    no_text = [e for e in entries if not e.get("has_text") and not e.get("error")]
    with_text = [e for e in entries if e.get("has_text")]
    not_in_zotero = [e for e in with_text if not e.get("zotero_found")]
    in_zotero = [e for e in with_text if e.get("zotero_found")]

    print("=== PDFs ohne Text-Layer (OCR nötig) ===")
    for e in no_text:
        print("-", e["rel_path"])
    print(f"Anzahl: {len(no_text)}\n")

    print("=== PDFs MIT Text, aber NICHT in Zotero gefunden ===")
    for e in not_in_zotero:
        print("-", e["rel_path"])
    print(f"Anzahl: {len(not_in_zotero)}\n")

    print("=== PDFs MIT Text und in Zotero gefunden ===")
    for e in in_zotero:
        zitems = e.get("zotero_items") or []
        keys = ", ".join(item.get("key", "?") for item in zitems)
        print(f"- {e['rel_path']}  (Zotero-Keys: {keys})")
    print(f"Anzahl: {len(in_zotero)}")


if __name__ == "__main__":
    main()