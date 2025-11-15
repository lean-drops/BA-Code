
#!/usr/bin/env python3
"""
Erzeugt den HTML-Report für die neueste Session in data/authors_data/session_*.

Findet die neueste Session automatisch, liest CSVs, rekonstruiert Labels aus nodes,
und rendert authors_report.html mit dem Template in config/authors_report_template.

Usage: direkt ausführen. Keine Parameter.
"""
from __future__ import annotations

import os
import pandas as pd  # type: ignore
from typing import Dict

from organizer.authors.authors_utils import (
    Author, detect_project_dir, write_html_report, log_info, log_warn
)


def _find_latest_session(data_root: str) -> str:
    cands = []
    for name in os.listdir(data_root):
        p = os.path.join(data_root, name)
        if os.path.isdir(p) and name.startswith("session_"):
            nodes = os.path.join(p, "authors_nodes.csv")
            edges = os.path.join(p, "authors_edges.csv")
            if os.path.isfile(nodes) and os.path.isfile(edges):
                cands.append((os.path.getmtime(p), p))
    if not cands:
        raise FileNotFoundError("Keine Session mit CSVs gefunden. Bitte erst Analyze laufen lassen.")
    cands.sort(key=lambda t: t[0], reverse=True)
    return cands[0][1]


def main() -> None:
    project_dir = detect_project_dir()
    data_dir = os.path.join(project_dir, "data", "authors_data")
    session_dir = _find_latest_session(data_dir)
    log_info(f"Neueste Session: {session_dir}")

    nodes_path = os.path.join(session_dir, "authors_nodes.csv")
    edges_path = os.path.join(session_dir, "authors_edges.csv")
    dfn = pd.read_csv(nodes_path, sep=";")
    dfe = pd.read_csv(edges_path, sep=";")

    # Autoren für Labels rekonstruieren
    authors: Dict[str, Author] = {}
    for aid, disp in zip(dfn["author_id"].astype(str), dfn["display"].astype(str)):
        authors[aid] = Author(aid, disp, tuple(), tuple(), tuple())

    write_html_report(session_dir, authors, dfe)
    log_info("Report fertig.")


if __name__ == "__main__":
    main()