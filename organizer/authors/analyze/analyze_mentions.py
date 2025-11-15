#!/usr/bin/env python3
"""
Analyse der Staging-Daten und Schreiben der finalen Outputs (identisch zu vorher).

Schreibt in neuen Session-Ordner:
  data/authors_data/session_*/authors_nodes.csv
  data/authors_data/session_*/authors_edges.csv
  data/authors_data/session_*/authors_network.gexf (falls networkx)
  data/authors_data/session_*/works_mentions.csv

Optional erstellt ein separater Schritt (report/build_report.py)
  data/authors_data/session_*/authors_report.html

Usage:
    Direkt ausführen. Erwartet Staging aus scrape/scrape_pdfs.py im Ordner:
      <PROJECT_DIR>/data/authors_data/_staging
"""
from __future__ import annotations

import os
import json
from typing import Dict, List, Tuple

from organizer.authors.authors_utils import (
    Author,
    Mention,
    detect_project_dir,
    environment_report,
    ensure_session_dir,
    write_session_meta,
    aggregate_edges,
    write_csvs,
    write_gexf,
    log_info,
    log_warn,
    HAVE_PANDAS,
)


def _load_staging(
    staging_root: str,
) -> Tuple[Dict[str, dict], List[dict], List[dict], Dict[str, str], str]:
    """
    Lädt alle Staging-Dateien aus scrape/scrape_pdfs.py.

    Returns:
        targets:      Autor-Ziele (ID -> Meta-Dict)
        mentions:     Roh-Mentions (Liste von Dicts)
        works:        Roh-Werk-Mentions (Liste von Dicts)
        file2author:  Mapping PDF -> Quell-Autor-ID
        base:         Basis-PDF-Ordner aus staging_meta (kann leer sein)
    """
    if not os.path.isdir(staging_root):
        raise FileNotFoundError(
            f"Staging-Verzeichnis existiert nicht: {staging_root}. "
            "Bitte zuerst scrape/scrape_pdfs.py ausführen."
        )

    tpath = os.path.join(staging_root, "targets_all.json")
    mpath = os.path.join(staging_root, "mentions.jsonl")
    wpath = os.path.join(staging_root, "works_hits.jsonl")
    f2a_path = os.path.join(staging_root, "file2author.json")
    meta_path = os.path.join(staging_root, "staging_meta.json")

    missing = [
        p
        for p in (tpath, mpath, f2a_path, meta_path)
        if not os.path.isfile(p)
    ]
    if missing:
        msg = "Staging unvollständig. Fehlende Dateien:\n  " + "\n  ".join(missing)
        msg += "\nBitte zuerst scrape/scrape_pdfs.py ausführen."
        raise FileNotFoundError(msg)

    with open(tpath, "r", encoding="utf-8") as f:
        targets: Dict[str, dict] = json.load(f)

    with open(f2a_path, "r", encoding="utf-8") as f:
        file2author: Dict[str, str] = json.load(f)

    with open(meta_path, "r", encoding="utf-8") as f:
        meta_obj: Dict[str, object] = json.load(f)

    mentions: List[dict] = []
    with open(mpath, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                mentions.append(json.loads(line))

    works: List[dict] = []
    if os.path.isfile(wpath):
        with open(wpath, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    works.append(json.loads(line))

    base = str(meta_obj.get("base_pdf_dir") or "")
    return targets, mentions, works, file2author, base


def _rebuild_authors(targets: Dict[str, dict]) -> Dict[str, Author]:
    """Rekonstruiert Author-Objekte aus dem Staging-Targets-Dict."""
    out: Dict[str, Author] = {}
    for aid, d in targets.items():
        out[aid] = Author(
            author_id=d["author_id"],
            display=d["display"],
            surnames=tuple(d["surnames"]),
            pattern_strs=tuple(d["pattern_strs"]),
            folded_surnames=tuple(d["folded_surnames"]),
        )
    return out


def analyze_from_staging(staging_root: str) -> str:
    """
    Führt die komplette Analyse auf Basis der Staging-Daten aus.

    Args:
        staging_root: Pfad zum Staging-Ordner
                      (<PROJECT_DIR>/data/authors_data/_staging)

    Returns:
        Pfad zum erzeugten Session-Ordner.
    """
    environment_report()
    if not HAVE_PANDAS:
        raise RuntimeError("pandas fehlt. Installiere mit: pip install pandas")

    targets, mentions_rows, works_rows, file2author, base = _load_staging(staging_root)

    pdfs = sorted(file2author.keys())
    if not pdfs:
        log_warn("Keine PDFs in file2author-Staging gefunden. Analyse wird dennoch fortgesetzt.")
    else:
        log_info(f"PDFs laut Staging: {len(pdfs)}")

    # Session-Ordner wie zuvor: Base-PDF-Ordner falls vorhanden, sonst Projektpfad
    session_base = base or detect_project_dir()
    session_dir = ensure_session_dir(session_base, pdfs)
    log_info(f"Session-Ordner: {session_dir}")

    # Mentions -> Dataclasses
    mentions: List[Mention] = [
        Mention(
            src_id=row["src"],
            tgt_id=row["tgt"],
            pdf_path=row["pdf_file"],
            page=int(row["page"]),
            in_bib=bool(int(row["in_bib"])),
            pattern=row["pattern"],
            context=row["context"],
        )
        for row in mentions_rows
    ]

    authors = _rebuild_authors(targets)

    # Aggregation & Standard-Exports (wie bisher)
    df_nodes, df_edges = aggregate_edges(authors, mentions)
    write_session_meta(session_dir, session_base, pdfs)
    write_csvs(session_dir, df_nodes, df_edges)
    write_gexf(session_dir, authors, df_edges)

    # Works CSV (falls vorhanden)
    if works_rows:
        import pandas as pd  # type: ignore

        dfw = pd.DataFrame(works_rows)
        out_path = os.path.join(session_dir, "works_mentions.csv")
        dfw.to_csv(out_path, index=False, sep=";")
        log_info(f"Werk-Mentions geschrieben: {out_path}")

        topw = (
            dfw.groupby("canonical")["weight"]
            .sum()
            .sort_values(ascending=False)
            .head(10)
        )
        if not topw.empty:
            log_info("Top-Werke:")
            for k, v in topw.items():
                print(f"  {k}  score={int(v)}")
    else:
        log_info("Keine Werk-Mentions im Staging vorhanden; works_mentions.csv wird nicht erzeugt.")

    # Kurzsummary Autoren
    if not df_edges.empty:
        log_info("Top-Kanten:")
        for _, r in (
            df_edges.sort_values("weighted", ascending=False)
            .head(10)
            .iterrows()
        ):
            src_id = r["src"]
            tgt_id = r["tgt"]
            sdisp = authors.get(src_id).display if src_id in authors else src_id
            tdisp = authors.get(tgt_id).display if tgt_id in authors else tgt_id
            print(
                f"  {sdisp} → {tdisp} | "
                f"total={r['total']} bib={r['bib_hits']} text={r['text_hits']}"
            )
    else:
        log_warn("Keine Autoren-Kanten gefunden.")

    log_info("Analyse fertig.")
    return session_dir


def main() -> None:
    """Entry-Point für den direkten Aufruf als Skript."""
    project_dir = detect_project_dir()
    staging_root = os.path.join(project_dir, "data", "authors_data", "_staging")
    analyze_from_staging(staging_root)


if __name__ == "__main__":
    main()