# organizer/chronik/chronik_finder/run.py
#!/usr/bin/env python3
"""
Orchestrator für den DB-basierten Chroniken-Finder.

Ablauf:
1) Projektpfade bestimmen und DB laden
2) Regex-Muster aus SQLite kompilieren
3) PDFs finden und scannen
4) Treffer aggregieren und Ausgaben schreiben:
   - chroniken_report.html
   - chroniken_network.gexf*  (*falls networkx vorhanden)
   - session_meta.json
   - Kanten in edges_ac / edges_cc

Öffentliche Funktion:
    run_finder(...) -> (session_dir | None, df | None, agg | None)

Kompatibel zum bisherigen Aufrufer 'chronik-search.py'.
"""
from __future__ import annotations

import os
import sqlite3
import traceback
from pathlib import Path
from typing import List, Optional, Tuple

from .aggregate import aggregate
from .constants import DEFAULT_SKIP_BIBLIOGRAPHY, MAX_WORKERS_DEFAULT
from .models import Hit
from .output import (
    ensure_session_dir,
    maybe_write_gexf,
    write_html,
    write_meta,
)
from .paths import config_db_path, default_pdf_dir, iter_pdfs, project_root
from .patterns import compile_patterns_from_db
from .scan import scan_pdfs

# DB-Helper nur für Chroniken-Kanten
from .chronik_db_io import insert_edges_ac, insert_edges_cc
from organizer.authors.db_io import create_search_run


def _print_env(root: Path, pdf_dir: Path, db_path: Path, skip_bib: bool, max_workers: int) -> None:
    print("[INFO] Chroniken-Finder (DB-Modus)")
    print("       root       =", root)
    print("       db_path    =", db_path)
    print("       pdf_dir    =", pdf_dir)
    print("       skip_bib   =", skip_bib)
    print("       workers    =", max_workers)


def _deduplicate_hits(hits: List[Hit]) -> List[Hit]:
    """
    Entfernt identische Treffer. Stabil für identischen Output.
    Schlüssel: (pdf_file, page, group, label, pattern, context)
    """
    seen: set = set()
    out: List[Hit] = []
    for h in hits:
        key = (
            getattr(h, "pdf_path", getattr(h, "pdf_file", "")),
            getattr(h, "page", 0),
            getattr(h, "group", ""),
            getattr(h, "label", ""),
            getattr(h, "pattern", ""),
            getattr(h, "context", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


def run_finder(
    pdf_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
    skip_bibliography: bool = DEFAULT_SKIP_BIBLIOGRAPHY,
    max_workers: int = MAX_WORKERS_DEFAULT,
) -> Tuple[Optional[Path], object, object]:
    """
    Führt den gesamten Pipeline-Lauf aus.
    Rückgabe: (session_dir | None, df | None, agg | None)
    """
    root = project_root()
    pdf_dir = pdf_dir or default_pdf_dir(root)
    db_path = db_path or config_db_path(root)
    _print_env(root, pdf_dir, db_path, skip_bibliography, max_workers)

    # 1) Patterns laden (inkl. chroniken_canon.patterns_json)
    try:
        patterns, weights = compile_patterns_from_db(db_path)
    except Exception as e:
        print(f"[ERROR] Konnte Muster aus DB nicht laden: {e}")
        traceback.print_exc()
        return None, None, None
    if not patterns:
        print("[ERROR] Keine gültigen Muster aus DB.")
        return None, None, None

    # 2) PDFs
    pdfs = list(iter_pdfs(pdf_dir))
    if not pdfs:
        print("[WARN] Keine PDFs gefunden.")
        return None, None, None

    # 3) Session
    session_dir = ensure_session_dir(root, pdfs)
    print(f"[INFO] Session-Ordner: {session_dir}")

    # 4) Scan
    try:
        hits = scan_pdfs(pdfs, patterns, max_workers=max_workers, skip_bib=skip_bibliography)
    except Exception as e:
        print(f"[ERROR] Scan fehlgeschlagen: {e}")
        traceback.print_exc()
        return None, None, None

    # 5) Dedup + Aggregation
    dedup = _deduplicate_hits(hits)
    df, agg = aggregate(dedup, weights)

    # 6) Outputs + DB-Kanten
    try:
        # Metadaten schreiben (Session-Verzeichnis, DB-Pfad etc.)
        write_meta(session_dir, root, pdf_dir, pdfs, db_path, weights)

        # Kanten direkt in DB eintragen
        try:
            with sqlite3.connect(str(db_path)) as conn:
                run_id = create_search_run(
                    conn,
                    kind="chronik_search",
                    session_dir=str(session_dir),
                )

                edges_cc = insert_edges_cc(conn, run_id, dedup, str(root))
                edges_ac = insert_edges_ac(conn, run_id, dedup, str(root))

                print(f"[INFO] DB-Einträge: edges_ac={edges_ac} edges_cc={edges_cc}")
        except Exception as e:
            print(f"[ERROR] DB-Insert fehlgeschlagen: {e}")
            traceback.print_exc()

        # Optional: Netzwerk und HTML generieren
        maybe_write_gexf(session_dir, df)
        write_html(session_dir, df, agg)
    except Exception as e:
        print(f"[ERROR] Schreiben der Outputs fehlgeschlagen: {e}")
        traceback.print_exc()
        return session_dir, df, agg

    # 7) Kurzer Überblick
    try:
        if agg is not None and len(agg) > 0:
            print("[INFO] Top-Labels nach Weighted:")
            view = agg.sort_values("weighted_mentions", ascending=False).head(15)
            for _, r in view.iterrows():
                print(
                    f"  [{r['group']}] {r['label']}: "
                    f"mentions={int(r['mentions'])} docs={int(r['docs'])} "
                    f"weighted={float(r['weighted_mentions'])}"
                )
        else:
            print("[INFO] Keine Treffer aggregiert.")
    except Exception:
        # Keine harte Abhängigkeit von pandas für diese Ausgabe
        pass

    return session_dir, df, agg