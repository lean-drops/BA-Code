# organizer/chronik/chronik_finder/patterns.py
#!/usr/bin/env python3
"""
Muster (Regex) aus SQLite-DB laden und kompilieren.

Quelle:
- Tabelle 'works_json' mit Spalten:
    - json_canonical (Label)
    - aliases_json   (JSON-Liste von Regex-Strings)
Optionale Quellen (wenn vorhanden, robust erkannt):
- Tabelle 'series_patterns' mit Spalten (canonical, aliases_json)
- Tabellen 'generic_terms' oder 'generic_patterns'
    - entweder Spalte 'pattern' (Text) ODER 'aliases_json' (JSON-Liste)

Rückgabe:
- Liste PatternEntry(label, group, regex)
- Gewichte-Dict {'work':..., 'series':..., 'generic':...}

Usage:
    # Debug: zählt kompilierte Muster aus der DB
    python -m organizer.chronik.chronik_finder.patterns
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

from .constants import BIB_HEADINGS, DEFAULT_WEIGHTS
from .models import PatternEntry


# ---------------- Hilfsfunktionen ----------------

def _flexify_spaces(alias_pat: str) -> str:
    """
    Erweitert echte Leerzeichen im Alias zu einer flexiblen Klasse: [\\s NBSP -]+
    Wichtig: Ersatz via Callback, damit Backreferences im Replacement nicht interpretiert werden.
    """
    nbsp = "\u00A0"  # echtes NBSP

    def repl(_m: re.Match) -> str:
        # Literal-Text für das Muster zurückgeben
        return f"[\\s{nbsp}\\-]+"

    return re.sub(r"\s+", repl, alias_pat)


def _compile_list(raw_list: Iterable[str], label: str, group: str, sink: List[PatternEntry]) -> None:
    for raw in raw_list:
        alias = str(raw)
        try:
            pat = _flexify_spaces(alias)
        except re.error as e:  # sehr selten, falls Replacement fehlschlägt
            print(f"[WARN] Alias-Ersatz fehlgeschlagen, nutze Rohpattern ({group}:{label}): {alias} ({e})")
            pat = alias
        try:
            rgx = re.compile(pat, re.IGNORECASE)
            sink.append(PatternEntry(label=label, group=group, regex=rgx))
        except re.error as e:
            print(f"[WARN] Ungültiges Regex ignoriert ({group}:{label}): {alias} ({e})")


def _table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (name,))
    return cur.fetchone() is not None


def _maybe_weights(cur: sqlite3.Cursor) -> Dict[str, float]:
    """
    Optionale Gewichte aus einer Tabelle 'weights' lesen:
      CREATE TABLE weights (group_name TEXT PRIMARY KEY, weight REAL);
    """
    if not _table_exists(cur, "weights"):
        return dict(DEFAULT_WEIGHTS)
    try:
        cur.execute("SELECT group_name, weight FROM weights;")
        rows = cur.fetchall()
        w: Dict[str, float] = dict(DEFAULT_WEIGHTS)
        for g, val in rows:
            try:
                w[str(g)] = float(val)
            except Exception:
                continue
        return w
    except Exception:
        return dict(DEFAULT_WEIGHTS)


# ---------------- Öffentliche API ----------------

def compile_patterns_from_db(db_path: Path) -> Tuple[List[PatternEntry], Dict[str, float]]:
    """
    Liest Regex-Muster aus SQLite und kompiliert sie.

    :param db_path: Pfad zu 'chroniken.sqlite3'
    :return: (compiled_patterns, weights)
    """
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite nicht gefunden: {db_path}")

    compiled: List[PatternEntry] = []
    with sqlite3.connect(str(db_path)) as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        # Gewichte
        weights = _maybe_weights(cur)

        # --- works_json ---
        if not _table_exists(cur, "works_json"):
            raise RuntimeError("Tabelle 'works_json' fehlt in der Datenbank.")
        cur.execute(
            "SELECT COALESCE(json_canonical,'') AS label, "
            "       COALESCE(aliases_json,'[]') AS aliases_json "
            "FROM works_json;"
        )
        for row in cur.fetchall():
            label = str(row["label"]).strip()
            try:
                aliases = json.loads(row["aliases_json"]) or []
                if not isinstance(aliases, list):
                    aliases = []
            except Exception:
                aliases = []
            _compile_list(aliases, label=label or "work", group="work", sink=compiled)

        # --- series (optional) ---
        if _table_exists(cur, "series_patterns"):
            cur.execute(
                "SELECT COALESCE(canonical,'') AS label, "
                "       COALESCE(aliases_json,'[]') AS aliases_json "
                "FROM series_patterns;"
            )
            for row in cur.fetchall():
                label = str(row["label"]).strip() or "series"
                try:
                    aliases = json.loads(row["aliases_json"]) or []
                    if not isinstance(aliases, list):
                        aliases = []
                except Exception:
                    aliases = []
                _compile_list(aliases, label=label, group="series", sink=compiled)

        # --- generic terms (optional, mehrere mögliche Schemata) ---
        def _add_generic_from_patterns_table(tbl: str) -> None:
            cur.execute(f"SELECT pattern FROM {tbl};")
            for (pat,) in cur.fetchall():
                if pat:
                    _compile_list([str(pat)], label="generic", group="generic", sink=compiled)

        if _table_exists(cur, "generic_terms"):
            # a) pattern pro Zeile
            try:
                _add_generic_from_patterns_table("generic_terms")
            except Exception:
                # b) aliases_json
                try:
                    cur.execute("SELECT aliases_json FROM generic_terms;")
                    for (aj,) in cur.fetchall():
                        try:
                            arr = json.loads(aj) or []
                            if isinstance(arr, list):
                                _compile_list(arr, label="generic", group="generic", sink=compiled)
                        except Exception:
                            continue
                except Exception:
                    pass
        elif _table_exists(cur, "generic_patterns"):
            _add_generic_from_patterns_table("generic_patterns")

    print(f"[INFO] Kompilierte Regex: {len(compiled)} | Gewichte: {weights}")
    return compiled, weights


def compile_bib_heading_patterns() -> List[re.Pattern]:
    return [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in BIB_HEADINGS]


def detect_bibliography_pages(doc) -> Set[int]:
    from .text import normalize_text
    pats = compile_bib_heading_patterns()
    bib_pages: Set[int] = set()
    start_seen = False
    for i in range(doc.page_count):
        raw = (doc.load_page(i).get_text("text") or "")
        txt = normalize_text(raw)
        if not start_seen and any(p.search(txt) for p in pats):
            start_seen = True
        if start_seen:
            bib_pages.add(i)
    return bib_pages


# ---------------- Debug-Entry ----------------

def main() -> None:
    """Kleiner Debuglauf: lädt DB, kompiliert und zeigt eine Stichprobe."""
    from .paths import project_root, config_db_path
    root = project_root()
    db = config_db_path(root)
    print(f"[DEBUG] Root: {root}")
    print(f"[DEBUG] DB:   {db}")
    pats, weights = compile_patterns_from_db(db)
    print(f"[DEBUG] Muster gesamt: {len(pats)} | Gewichte: {weights}")
    for i, pe in enumerate(pats[:10], 1):
        print(f"  ({i}) [{pe.group}] {pe.label} :: /{pe.regex.pattern}/i")


if __name__ == "__main__":
    main()