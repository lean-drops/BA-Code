#!/usr/bin/env python3
"""
chronik_db_io.py

DB-Helfer für den Chroniken-Finder:

- Mapping von Treffern (Hits) auf Chroniken über chroniken_canon + chroniken
- Eintragen von:
    - edges_ac: Sekundärwerk (bibliography)  → Chronik (chroniken)
    - edges_cc: Chronik-PDF (chr_*.pdf)     → Chronik (chroniken)
    - edge_hits_ac: einzelne Treffer (mit Seitenzahl/Kontext) für AC-Kanten
    - edge_hits_cc: einzelne Treffer (mit Seitenzahl/Kontext) für CC-Kanten

Voraussetzungen (siehe build_chroniken_db_v4.py):
    - Tabelle chroniken (mit canonical_key, pdf_filename)
    - Tabelle chroniken_canon(kind, canonical, label, patterns_json, weight)
    - Tabelle bibliography(file_basename, file_relpath, canonical_key, ...)
    - Tabellen edges_ac, edges_cc
"""

from __future__ import annotations

import os
import re
import sqlite3
import unicodedata
from typing import Dict, Iterable, Tuple

from organizer.authors.db_io import ensure_bibliography_entry


# ---------------------------------------------------------------------------
# Normalisierung & canonical_key, identisch zu build_chroniken_db_v4.py
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "chronik", "chronicon", "schweizerchronik", "geschichte", "geschichten",
    "berner", "zuercher", "zürcher", "luzerner", "eidgenossenschaft",
    "helveticum", "helvetica", "eidgenössische", "eidgenossische",
}


def _normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _canonical_key(s: str) -> str:
    base = _normalize_text(s)
    base = re.sub(r"[^a-z0-9]+", " ", base)
    base = " ".join(t for t in base.split() if t not in _STOPWORDS)
    return re.sub(r"\s+", " ", base).strip()


# ---------------------------------------------------------------------------
# Edge-Hits-Tabellen bei Bedarf automatisch anlegen
# ---------------------------------------------------------------------------

def _ensure_edge_hits_tables(conn: sqlite3.Connection) -> None:
    """
    Legt die Tabellen edge_hits_ac und edge_hits_cc an, falls sie noch nicht existieren.
    """
    cur = conn.cursor()

    # AC-Hits: Sekundärwerk → Chronik mit Seiten & Kontext
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS edge_hits_ac (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            bibliography_id INTEGER NOT NULL,
            chronik_id INTEGER NOT NULL,
            page INTEGER,
            pattern TEXT,
            context TEXT,
            FOREIGN KEY(run_id) REFERENCES search_runs(id),
            FOREIGN KEY(bibliography_id) REFERENCES bibliography(id),
            FOREIGN KEY(chronik_id) REFERENCES chroniken(id)
        );
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_edge_hits_ac_main
        ON edge_hits_ac(run_id, bibliography_id, chronik_id, page);
        """
    )

    # CC-Hits: Chronik → Chronik mit Seiten & Kontext
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS edge_hits_cc (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            from_id INTEGER NOT NULL,
            to_id INTEGER NOT NULL,
            page INTEGER,
            pattern TEXT,
            context TEXT,
            FOREIGN KEY(run_id) REFERENCES search_runs(id),
            FOREIGN KEY(from_id) REFERENCES chroniken(id),
            FOREIGN KEY(to_id) REFERENCES chroniken(id)
        );
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_edge_hits_cc_main
        ON edge_hits_cc(run_id, from_id, to_id, page);
        """
    )

    conn.commit()


# ---------------------------------------------------------------------------
# Index über Chroniken + Chroniken-Canon
# ---------------------------------------------------------------------------

def _build_chronik_index(conn: sqlite3.Connection) -> Tuple[Dict[str, int], Dict[str, int]]:
    """
    Liefert:
      - ck_map: canonical_key -> chroniken.id
      - label_map: norm(label) -> chroniken.id

    Dabei wird chroniken_canon.canonical via canonical_key an chroniken.canonical_key
    gematcht. label wird mit normalize_text() normalisiert.
    """
    cur = conn.cursor()
    ck_map: Dict[str, int] = {}
    label_map: Dict[str, int] = {}

    # 1) alle Chroniken in ck_map
    try:
        cur.execute("SELECT id, canonical_key FROM chroniken;")
        for cid, ck in cur.fetchall():
            if ck:
                ck_map[str(ck)] = int(cid)
    except Exception:
        return ck_map, label_map

    # 2) chroniken_canon: label → canonical_key → id
    try:
        cur.execute(
            "SELECT kind, canonical, label FROM chroniken_canon "
            "WHERE kind = 'work';"
        )
        rows = cur.fetchall()
    except Exception:
        rows = []

    for kind, canonical, label in rows:
        can = (canonical or "").strip()
        lab = (label or "").strip()
        if not can and not lab:
            continue
        ck = _canonical_key(can or lab)
        chronik_id = ck_map.get(ck)
        if not chronik_id:
            continue
        ln = _normalize_text(lab or can)
        if ln and ln not in label_map:
            label_map[ln] = chronik_id

    return ck_map, label_map


# ---------------------------------------------------------------------------
# edges_ac: Sekundärwerk → Chronik (+ edge_hits_ac)
# ---------------------------------------------------------------------------

def insert_edges_ac(
    conn: sqlite3.Connection,
    run_id: int,
    hits: Iterable[object],
    project_root: str,
) -> int:
    """
    Fügt Kanten in edges_ac ein (Sekundärwerk → Chronik) UND pro Treffer
    einen Eintrag in edge_hits_ac (inkl. Seitenzahl/Kontext).

    Verwendet nur Treffer der Gruppe 'work', deren Label via chroniken_canon/chroniken
    einer konkreten Chronik zugeordnet werden kann.

    Args:
        conn: offene SQLite-Verbindung
        run_id: search_runs.id
        hits: Iterable von Hit-ähnlichen Objekten (Attr: pdf_path/pdf_file, group, label,
              pattern, context, page)
        project_root: Projektwurzel (für relative Pfade in bibliography)

    Returns:
        Anzahl eingefügter Kanten (Paar (bibliography_id, chronik_id)) in edges_ac
        (die Anzahl Hits in edge_hits_ac kann größer sein)
    """
    cur = conn.cursor()

    # Sicherstellen, dass die Hits-Tabellen vorhanden sind
    _ensure_edge_hits_tables(conn)

    ck_map, label_map = _build_chronik_index(conn)
    if not ck_map and not label_map:
        return 0

    # Aggregation für edges_ac: (bib_id, chronik_id) -> (weight, first_pattern)
    agg: Dict[Tuple[int, int], Tuple[float, str]] = {}

    insert_hit_sql = """
        INSERT INTO edge_hits_ac
            (run_id, bibliography_id, chronik_id, page, pattern, context)
        VALUES (?, ?, ?, ?, ?, ?);
    """

    for h in hits:
        grp = getattr(h, "group", None) or ""
        if grp != "work":
            continue

        pdf_path = getattr(h, "pdf_path", None) or getattr(h, "pdf_file", None)
        label = getattr(h, "label", None)
        pat = getattr(h, "pattern", "") or ""
        ctx = getattr(h, "context", "") or ""
        page = getattr(h, "page", None)

        if not pdf_path or not label:
            continue

        # Chronik-Mapping
        ln = _normalize_text(str(label).strip())
        chronik_id = label_map.get(ln)
        if not chronik_id:
            ck = _canonical_key(str(label).strip())
            chronik_id = ck_map.get(ck)
        if not chronik_id:
            continue

        # Bibliography
        try:
            bib_id = ensure_bibliography_entry(conn, project_root, str(pdf_path), None)
        except Exception:
            continue

        # Aggregierte Kante zählen
        key = (bib_id, chronik_id)
        w_prev, src_prev = agg.get(key, (0.0, ""))
        agg[key] = (w_prev + 1.0, pat or src_prev)

        # Einzelnen Hit in edge_hits_ac schreiben
        try:
            cur.execute(
                insert_hit_sql,
                (
                    run_id,
                    bib_id,
                    chronik_id,
                    int(page) if page is not None else None,
                    pat,
                    ctx,
                ),
            )
        except Exception:
            # Fehler bei einzelnen Hits nicht fatal
            continue

    # Aggregierte edges_ac schreiben
    inserted = 0
    insert_edge_sql = """
        INSERT INTO edges_ac
            (run_id, bibliography_id, chronik_id, weight, raw_source)
        VALUES (?, ?, ?, ?, ?);
    """
    for (bib_id, chronik_id), (w, src) in agg.items():
        try:
            cur.execute(
                insert_edge_sql,
                (run_id, bib_id, chronik_id, float(w), src),
            )
            inserted += 1
        except Exception:
            continue

    conn.commit()
    return inserted


# ---------------------------------------------------------------------------
# edges_cc: Chronik → Chronik (+ edge_hits_cc)
# ---------------------------------------------------------------------------

def insert_edges_cc(
    conn: sqlite3.Connection,
    run_id: int,
    hits: Iterable[object],
    project_root: str,  # ungenutzt, behalten für API-Konsistenz
) -> int:
    """
    Fügt Chronik→Chronik-Kanten in edges_cc ein UND pro Treffer
    einen Eintrag in edge_hits_cc (inkl. Seitenzahl/Kontext).

    Quelle:
        - Nur Treffer aus PDFs, deren basename mit 'chr_' oder 'chr-' beginnt
        - Nur Treffer der Gruppe 'work'
        - Quelle (from_id) wird über chroniken.pdf_filename ermittelt
        - Ziel (to_id) über Label/canonical_key (wie in insert_edges_ac)

    Args:
        conn: offene SQLite-Verbindung
        run_id: search_runs.id
        hits: Iterable von Hit-ähnlichen Objekten (Attr: pdf_path/pdf_file, group, label,
              pattern, context, page)
        project_root: Projektwurzel (hier nicht zwingend nötig)

    Returns:
        Anzahl eingefügter Kanten (Paar (from_id, to_id)) in edges_cc
        (die Anzahl Hits in edge_hits_cc kann größer sein)
    """
    cur = conn.cursor()

    # Sicherstellen, dass die Hits-Tabellen vorhanden sind
    _ensure_edge_hits_tables(conn)

    ck_map, label_map = _build_chronik_index(conn)
    if not ck_map and not label_map:
        return 0

    # Mapping pdf_filename -> chroniken.id (Quelle)
    pdf_map: Dict[str, int] = {}
    try:
        cur.execute(
            "SELECT id, pdf_filename FROM chroniken "
            "WHERE pdf_filename IS NOT NULL AND pdf_filename != '';"
        )
        for cid, fn in cur.fetchall():
            base = os.path.basename(str(fn)).lower()
            if base:
                pdf_map[base] = int(cid)
    except Exception:
        pdf_map = {}

    if not pdf_map:
        return 0

    # Aggregation für edges_cc: (from_id, to_id) -> (weight, first_pattern)
    agg: Dict[Tuple[int, int], Tuple[float, str]] = {}

    insert_hit_sql = """
        INSERT INTO edge_hits_cc
            (run_id, from_id, to_id, page, pattern, context)
        VALUES (?, ?, ?, ?, ?, ?);
    """

    for h in hits:
        grp = getattr(h, "group", None) or ""
        if grp != "work":
            continue

        pdf_path = getattr(h, "pdf_path", None) or getattr(h, "pdf_file", None)
        label = getattr(h, "label", None)
        pat = getattr(h, "pattern", "") or ""
        ctx = getattr(h, "context", "") or ""
        page = getattr(h, "page", None)

        if not pdf_path or not label:
            continue

        base = os.path.basename(str(pdf_path)).lower()
        if not (base.startswith("chr_") or base.startswith("chr-")):
            continue

        from_id = pdf_map.get(base)
        if not from_id:
            continue

        # Zielchronik
        ln = _normalize_text(str(label).strip())
        to_id = label_map.get(ln)
        if not to_id:
            ck = _canonical_key(str(label).strip())
            to_id = ck_map.get(ck)
        if not to_id or to_id == from_id:
            continue

        key = (from_id, to_id)
        w_prev, src_prev = agg.get(key, (0.0, ""))
        agg[key] = (w_prev + 1.0, pat or src_prev)

        # Einzelnen Hit in edge_hits_cc schreiben
        try:
            cur.execute(
                insert_hit_sql,
                (
                    run_id,
                    from_id,
                    to_id,
                    int(page) if page is not None else None,
                    pat,
                    ctx,
                ),
            )
        except Exception:
            continue

    # Aggregierte edges_cc schreiben
    inserted = 0
    insert_edge_sql = """
        INSERT INTO edges_cc
            (run_id, from_id, to_id, weight, raw_source)
        VALUES (?, ?, ?, ?, ?);
    """
    for (from_id, to_id), (w, src) in agg.items():
        try:
            cur.execute(
                insert_edge_sql,
                (run_id, from_id, to_id, float(w), src),
            )
            inserted += 1
        except Exception:
            continue

    conn.commit()
    return inserted