# organizer/authors/db_io.py
"""
DB-IO für Autoren/Works + Inserts in edges_aa/bibliography.

Dependencies: sqlite3
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import unicodedata
from datetime import datetime
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

from organizer.authors.authors_utils import (
    Author,  # dataclass: author_id, display, surnames, pattern_strs, folded_surnames
    strip_diacritics,
    make_diacritic_regex,
)

# --------------------------- helpers ---------------------------

def _tokens(cid: str) -> List[str]:
    return [x for x in cid.replace("_", "-").split("-") if x]

def _surnames(tokens: List[str]) -> List[str]:
    out: List[str] = []
    if tokens:
        out.append(tokens[0])
        if tokens[-1] != tokens[0]:
            out.append(tokens[-1])
    return [s for s in out if len(s) > 1 and not s.isdigit()]

def _canonical_key(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _relpath(project_root: str, abs_path: str) -> str:
    try:
        return os.path.relpath(abs_path, project_root)
    except Exception:
        return abs_path

def _ensure_json_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return list(v) if isinstance(v, list) else []
    except Exception:
        return []

# --------------------------- loaders ---------------------------

def fetch_authors(conn: sqlite3.Connection) -> Tuple[Dict[str, Author], Dict[str, dict], Dict[str, str]]:
    """
    Lädt Autoren aus der Tabelle 'authors' und erzeugt dieselben Strukturen wie build_authors_from_canon().
    Nutzt: canonical_id, display, filename_aliases_json, text_aliases_json, negatives_json, merge_into.
    """
    cur = conn.cursor()
    cur.execute("""SELECT canonical_id, display, filename_aliases_json, text_aliases_json, negatives_json, merge_into
                   FROM authors""")
    rows = cur.fetchall()

    merge_map: Dict[str, str] = {}
    for cid, _disp, _fa, _ta, _neg, mi in rows:
        if mi and mi != cid:
            merge_map[cid] = mi

    authors: Dict[str, Author] = {}
    aux: Dict[str, dict] = {}

    for cid, disp, fa, ta, neg, mi in rows:
        if not cid:
            continue
        if cid in merge_map and merge_map[cid] != cid:
            continue

        toks = _tokens(cid)
        surs = _surnames(toks)
        pattern_strs: List[str] = []

        # Heuristik-Patterns (Nachname, Vorname | Vorname Nachname | Name + Jahr)
        for sur in dict.fromkeys(surs):
            srx = make_diacritic_regex(sur)
            pat1 = rf"\b{srx}\b\s*,\s*[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\.\-]{{1,}}"
            pat2 = rf"\b[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\.\-]{{1,}}\s+{srx}\b"
            pat3 = rf"\b{srx}\b[^\n]{{0,30}}\b(1[4-9]\d{{2}}|20\d{{2}})\b"
            pattern_strs.extend([pat1, pat2, pat3])

        # Explizite Text-Aliase aus DB
        for rx in _ensure_json_list(ta):
            if isinstance(rx, str) and rx:
                pattern_strs.append(rx)

        display = disp or ", ".join(reversed([t.capitalize() for t in toks])) or cid
        folded = tuple(strip_diacritics(s).casefold() for s in dict.fromkeys(surs))

        authors[cid] = Author(
            author_id=cid,
            display=display,
            surnames=tuple(dict.fromkeys(surs)),
            pattern_strs=tuple(dict.fromkeys(pattern_strs)),
            folded_surnames=folded,
        )
        aux[cid] = {
            "negatives": _ensure_json_list(neg),
            "filename_aliases": _ensure_json_list(fa),
            "text_aliases": _ensure_json_list(ta),
        }

    return authors, aux, merge_map

def fetch_work_specs(conn: sqlite3.Connection) -> List[SimpleNamespace]:
    """
    Lädt Works aus 'works_canon' (nur Typ 'work').
    Gibt eine Liste Objekte mit Attributen (canonical, kind, patterns, negatives, weight) zurück.
    """
    cur = conn.cursor()
    weights = {"work": 1.0, "series": 1.0, "generic": 1.2}
    try:
        cur.execute("SELECT weights_json FROM works_canon_config WHERE id=1")
        row = cur.fetchone()
        if row and row[0]:
            weights.update(json.loads(row[0]) or {})
    except Exception:
        pass

    cur.execute("""SELECT canonical, aliases_json, negatives_json FROM works_canon""")
    specs: List[SimpleNamespace] = []
    for canonical, aliases_json, negatives_json in cur.fetchall():
        aliases = _ensure_json_list(aliases_json)
        negatives = _ensure_json_list(negatives_json)
        if not canonical or not aliases:
            continue
        specs.append(SimpleNamespace(
            canonical=canonical,
            kind="work",
            patterns=aliases,
            negatives=negatives,
            weight=float(weights.get("work", 1.0)),
        ))
    return specs

# --------------------------- ensure/create ---------------------------

def ensure_author(conn: sqlite3.Connection, canonical_id: str, display: Optional[str] = None) -> int:
    """
    Sichert, dass ein Autor in 'authors' existiert; legt minimalen Datensatz an, falls nicht vorhanden.
    """
    cur = conn.cursor()
    cur.execute("SELECT id FROM authors WHERE canonical_id=?", (canonical_id,))
    row = cur.fetchone()
    if row:
        return int(row[0])
    display = display or ", ".join(reversed([t.capitalize() for t in _tokens(canonical_id)])) or canonical_id
    cur.execute("""INSERT INTO authors
                   (canonical_id, display, filename_aliases_json, text_aliases_json, negatives_json, merge_into)
                   VALUES (?, ?, '[]', '[]', '[]', NULL)""", (canonical_id, display))
    return int(cur.lastrowid)

def ensure_bibliography_entry(conn: sqlite3.Connection, project_root: str,
                              pdf_path: str, canonical_author_id: Optional[str]) -> int:
    """
    Sichert, dass ein Eintrag in 'bibliography' existiert und die Zuordnung in 'bibliography_authors'.
    """
    cur = conn.cursor()
    base = os.path.basename(pdf_path)

    cur.execute("SELECT id FROM bibliography WHERE file_basename=?", (base,))
    row = cur.fetchone()
    if row:
        bib_id = int(row[0])
    else:
        rel = _relpath(project_root, pdf_path)
        key = _canonical_key(os.path.splitext(base)[0])
        cur.execute("""INSERT INTO bibliography (file_basename, file_relpath, canonical_key, pdf_present)
                       VALUES (?, ?, ?, 1)""", (base, rel, key))
        bib_id = int(cur.lastrowid)

    if canonical_author_id:
        aid = ensure_author(conn, canonical_author_id)
        cur.execute("""INSERT OR IGNORE INTO bibliography_authors (bibliography_id, author_id)
                       VALUES (?, ?)""", (bib_id, aid))
    return bib_id

def ensure_bibliography_work(conn: sqlite3.Connection, bibliography_id: int, work_canonical: str) -> Optional[int]:
    """
    Verknüpft bibliography_id mit einem Werk aus 'works_canon'. Legt minimalen Work-Eintrag an, wenn nötig.
    """
    cur = conn.cursor()
    cur.execute("SELECT id FROM works_canon WHERE canonical=?", (work_canonical,))
    row = cur.fetchone()
    if not row:
        cur.execute("""INSERT INTO works_canon (canonical, canonical_key, author, year, aliases_json, negatives_json)
                       VALUES (?, ?, NULL, NULL, '[]', '[]')""",
                    (work_canonical, _canonical_key(work_canonical)))
        wid = int(cur.lastrowid)
    else:
        wid = int(row[0])

    cur.execute("""INSERT OR IGNORE INTO bibliography_works (bibliography_id, work_id)
                   VALUES (?, ?)""", (bibliography_id, wid))
    return wid

def create_search_run(conn: sqlite3.Connection, kind: str,
                      session_dir: Optional[str] = None, notes: Optional[str] = None) -> int:
    cur = conn.cursor()
    cur.execute("""INSERT INTO search_runs (kind, started_at, session_dir, notes)
                   VALUES (?, ?, ?, ?)""",
                (kind, datetime.now().isoformat(timespec="seconds"), session_dir, notes))
    return int(cur.lastrowid)

# --------------------------- inserts: edges_aa ---------------------------

def insert_edges_aa(conn: sqlite3.Connection, run_id: int,
                    file2author: Dict[str, str],
                    mentions: List[object],
                    project_root: str) -> int:
    """
    Aggregiert Mentions zu Kanten und schreibt in edges_aa.
    Nutzt reale PDF (Quelle) + virtuellen 'Autor-PDF'-Placeholder als Ziel.
    Rückgabe: Anzahl eingefügter Kanten.
    """
    cur = conn.cursor()

    bib_cache: Dict[str, int] = {}
    edge_weight: Dict[Tuple[int, int], float] = {}

    def _ensure_src(pdf_path: str) -> int:
        if pdf_path in bib_cache:
            return bib_cache[pdf_path]
        src_author = file2author.get(pdf_path)
        bid = ensure_bibliography_entry(conn, project_root, pdf_path, src_author)
        bib_cache[pdf_path] = bid
        return bid

    def _ensure_tgt(author_id: str) -> int:
        virt_base = f"VIRT_AUTHOR_{author_id}.pdf"
        virt_path = os.path.join(project_root, "data", "virtual", "authors", virt_base)
        os.makedirs(os.path.dirname(virt_path), exist_ok=True)
        if virt_path in bib_cache:
            return bib_cache[virt_path]
        ensure_author(conn, author_id)
        bid = ensure_bibliography_entry(conn, project_root, virt_path, author_id)
        bib_cache[virt_path] = bid
        return bid

    for m in mentions:
        try:
            src_bib = _ensure_src(m.pdf_path)
            tgt_bib = _ensure_tgt(m.tgt_id)
            key = (src_bib, tgt_bib)
            edge_weight[key] = edge_weight.get(key, 0.0) + 1.0
        except Exception:
            continue

    inserted = 0
    for (src_bib, tgt_bib), w in edge_weight.items():
        cur.execute("""INSERT INTO edges_aa
                       (run_id, from_bib_id, to_bib_id, weight, raw_source)
                       VALUES (?, ?, ?, ?, ?)""",
                    (run_id, src_bib, tgt_bib, float(w), "authors_search"))
        inserted += 1

    conn.commit()
    return inserted