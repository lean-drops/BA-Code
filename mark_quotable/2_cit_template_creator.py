# /Users/programming/PycharmProjects/BA-Codes/create_citation_template.py
"""
Erzeugt aus analysis_report.json ein Template (citations_template.json),
in dem du für jeden belegpflichtigen Satz eine Fussnote eintragen kannst.

Input:
    analysis_report.json  – Output deines Markier-Scripts
Output:
    citations_template.json – Liste von Dicts mit:
        paragraph_index, sentence_index, sentence_text, footnote (leer)
"""

import json
from pathlib import Path

# Konfiguration
ANALYSIS_REPORT = Path("data/analysis_report.json")
TEMPLATE_JSON = Path("data/citations_template.json")


def build_template():
    if not ANALYSIS_REPORT.exists():
        raise FileNotFoundError(f"{ANALYSIS_REPORT} nicht gefunden")

    with ANALYSIS_REPORT.open("r", encoding="utf-8") as f:
        report = json.load(f)

    template = []
    for para_entry in report:
        p_idx = para_entry["paragraph_index"]
        for s in para_entry["sentences"]:
            if s.get("needs_citation"):
                template.append(
                    {
                        "paragraph_index": p_idx,
                        "sentence_index": s["index"],
                        "sentence_text": s["text"],
                        # Hier trägst du später die fertige Fussnote ein
                        # z.B. "Max Mustermann, Titel. Untertitel (Zürich 2020), S. 123–125."
                        "footnote": "",
                    }
                )

    with TEMPLATE_JSON.open("w", encoding="utf-8") as f:
        json.dump(template, f, ensure_ascii=False, indent=2)

    print(f"[OK] {len(template)} Einträge nach {TEMPLATE_JSON} geschrieben.")


if __name__ == "__main__":
    build_template()