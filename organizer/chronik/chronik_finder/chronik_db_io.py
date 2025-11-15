#!/usr/bin/env python3
"""
chronik_db_io.py

DB-Helfer für den Chroniken-Finder:

- Mapping von Treffern (Hits) auf Chroniken über chroniken_canon + chroniken
- Eintragen von:
    - edges_ac: Sekundärwerk (bibliography)  → Chronik (chroniken)
    - edges_cc: Chronik-PDF (chr_*.pdf)     → Chronik (chroniken)

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
# edges_ac: Sekundärwerk → Chronik
# ---------------------------------------------------------------------------

def insert_edges_ac(
    conn: sqlite3.Connection,
    run_id: int,
    hits: Iterable[object],
    project_root: str,
) -> int:
    """
    Fügt Kanten in edges_ac ein (Sekundärwerk → Chronik).

    Verwendet nur Treffer der Gruppe 'work', deren Label via chroniken_canon/chroniken
    einer konkreten Chronik zugeordnet werden kann.

    Args:
        conn: offene SQLite-Verbindung
        run_id: search_runs.id
        hits: Iterable von Hit-ähnlichen Objekten (Attr: pdf_path/pdf_file, group, label, pattern)
        project_root: Projektwurzel (für relative Pfade in bibliography)

    Returns:
        Anzahl eingefügter Kanten (Paar (bibliography_id, chronik_id))
    """
    cur = conn.cursor()
    ck_map, label_map = _build_chronik_index(conn)
    if not ck_map and not label_map:
        return 0

    agg: Dict[Tuple[int, int], Tuple[float, str]] = {}

    for h in hits:
        grp = getattr(h, "group", None) or ""
        if grp != "work":
            continue

        pdf_path = getattr(h, "pdf_path", None) or getattr(h, "pdf_file", None)
        label = getattr(h, "label", None)
        pat = getattr(h, "pattern", "") or ""
        if not pdf_path or not label:
            continue

        ln = _normalize_text(str(label).strip())
        chronik_id = label_map.get(ln)
        if not chronik_id:
            ck = _canonical_key(str(label).strip())
            chronik_id = ck_map.get(ck)
        if not chronik_id:
            continue

        try:
            bib_id = ensure_bibliography_entry(conn, project_root, str(pdf_path), None)
        except Exception:
            continue

        key = (bib_id, chronik_id)
        w_prev, src_prev = agg.get(key, (0.0, ""))
        agg[key] = (w_prev + 1.0, pat or src_prev)

    inserted = 0
    for (bib_id, chronik_id), (w, src) in agg.items():
        try:
            cur.execute(
                """
                INSERT INTO edges_ac
                    (run_id, bibliography_id, chronik_id, weight, raw_source)
                VALUES (?, ?, ?, ?, ?);
                """,
                (run_id, bib_id, chronik_id, float(w), src),
            )
            inserted += 1
        except Exception:
            continue

    conn.commit()
    return inserted


# ---------------------------------------------------------------------------
# edges_cc: Chronik → Chronik (nur wenn PDF-Name mit chr_/chr- beginnt)
# ---------------------------------------------------------------------------

def insert_edges_cc(
    conn: sqlite3.Connection,
    run_id: int,
    hits: Iterable[object],
    project_root: str,
) -> int:
    """
    Fügt Chronik→Chronik-Kanten in edges_cc ein.

    Quelle:
        - Nur Treffer aus PDFs, deren basename mit 'chr_' oder 'chr-' beginnt
        - Nur Treffer der Gruppe 'work'
        - Quelle (from_id) wird über chroniken.pdf_filename ermittelt
        - Ziel (to_id) über Label/canonical_key (wie in insert_edges_ac)

    Args:
        conn: offene SQLite-Verbindung
        run_id: search_runs.id
        hits: Iterable von Hit-ähnlichen Objekten
        project_root: Projektwurzel (hier nicht zwingend nötig, aber für Konsistenz beibehalten)

    Returns:
        Anzahl eingefügter Kanten (Paar (from_id, to_id))
    """
    cur = conn.cursor()
    ck_map, label_map = _build_chronik_index(conn)
    if not ck_map and not label_map:
        return 0

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

    agg: Dict[Tuple[int, int], Tuple[float, str]] = {}
    for h in hits:
        grp = getattr(h, "group", None) or ""
        if grp != "work":
            continue

        pdf_path = getattr(h, "pdf_path", None) or getattr(h, "pdf_file", None)
        label = getattr(h, "label", None)
        pat = getattr(h, "pattern", "") or ""
        if not pdf_path or not label:
            continue

        base = os.path.basename(str(pdf_path)).lower()
        if not (base.startswith("chr_") or base.startswith("chr-")):
            continue

        from_id = pdf_map.get(base)
        if not from_id:
            continue

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

    inserted = 0
    for (from_id, to_id), (w, src) in agg.items():
        try:
            cur.execute(
                """
                INSERT INTO edges_cc
                    (run_id, from_id, to_id, weight, raw_source)
                VALUES (?, ?, ?, ?, ?);
                """,
                (run_id, from_id, to_id, float(w), src),
            )
            inserted += 1
        except Exception:
            continue

    conn.commit()
    return inserted