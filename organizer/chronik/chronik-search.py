#!/usr/bin/env python3
"""
DB-basierter Einstiegspunkt für den Chroniken-Finder.

Dieses Skript ruft run_finder() aus dem Chroniken-Finder, verarbeitet die
Ergebnisse anschließend direkt in der SQLite-Datenbank.

Funktionen:
- Für alle Treffer aus dem Chroniken-Scan:
    - Es werden ausschließlich Kanten Chronik → Chronik in edges_cc
      eingetragen (keine Bibliography-/Authors-/Works-Einträge).
    - Quellen sind TXT/PDF aus dem Chroniken-Korpus.
    - Ziele sind Chroniken, die über chroniken_canon-Patterns erkannt wurden.
- Zusätzlich werden:
    - chroniken_texts gefüllt:
        * role='original_pdf'  → Verbindung Chronik ↔ Original-PDF (falls vorhanden)
        * role='chronik_txt'   → Verbindung Chronik ↔ chr_*.txt-Transkript
    - chroniken_canon_links gefüllt:
        * Verbindung chroniken_canon (Canon-Eintrag) ↔ chroniken (Werk)

Wichtige Tabellen:
- chroniken                (Quelle und Ziel für Chronik-IDs)
- chroniken_canon          (liefert die Muster, aus denen run_finder() seine Regex baut)
- chroniken_texts          (Chronik ↔ Dokument/Edition)
- chroniken_canon_links    (Chronik ↔ Canon-Eintrag)
- edges_cc                 (Chronik → Chronik Kanten)
- search_runs              (für den Lauf-Typ 'chronik_search')

Nutzung:
  python chronik-search.py

Gemeinsame Runs:
  Wenn die Umgebungsvariable SEARCH_RUN_ID gesetzt ist, wird diese ID als
  run_id verwendet (kein neuer Eintrag in search_runs). Andernfalls wird
  via create_search_run ein neuer Lauf angelegt (kind='chronik_search').
"""
from __future__ import annotations

import os
import re
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from organizer.chronik.chronik_finder.run import run_finder
from organizer.chronik.chronik_finder.paths import project_root, config_db_path

from organizer.authors.db_io import (
    create_search_run,
)


# ---------------------------------------------------------------------------
# Normalisierung / canonical_key (kompatibel zu build_chroniken_db_v4.py)
# ---------------------------------------------------------------------------

STOPWORDS: Set[str] = {
    "chronik",
    "chronicon",
    "schweizerchronik",
    "geschichte",
    "geschichten",
    "berner",
    "zuercher",
    "zürcher",
    "luzerner",
    "eidgenossenschaft",
    "helveticum",
    "helvetica",
    "eidgenössische",
    "eidgenossische",
    # technisch:
    "chr",
}


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def canonical_key(s: str) -> str:
    base = normalize_text(s)
    base = re.sub(r"[^a-z0-9]+", " ", base)
    base = " ".join(t for t in base.split() if t not in STOPWORDS)
    return re.sub(r"\s+", " ", base).strip()


# ---------------------------------------------------------------------------
# Chroniken-Lookup aus der DB
# ---------------------------------------------------------------------------

@dataclass
class ChronikLookup:
    by_basename: Dict[str, int]
    by_ckey: Dict[str, int]
    tokens_by_id: Dict[int, Set[str]]


def _load_chronik_lookup(conn: sqlite3.Connection) -> ChronikLookup:
    """
    Lädt Mapping von chroniken.id anhand:
    - pdf_filename → id (per Basename)
    - canonical_key → id
    - tokens_by_id: Tokenmenge von canonical_key pro id (für Fuzzy-Fallback)

    Erwartet Tabelle 'chroniken' mit Spalten:
        id, canonical_key, pdf_filename
    """
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, canonical_key, pdf_filename FROM chroniken;")
    except sqlite3.OperationalError as e:
        print(f"[WARN] Konnte Tabelle 'chroniken' nicht lesen: {e}")
        return ChronikLookup(by_basename={}, by_ckey={}, tokens_by_id={})

    by_basename: Dict[str, int] = {}
    by_ckey: Dict[str, int] = {}
    tokens_by_id: Dict[int, Set[str]] = {}

    for cid, ckey, pdf_filename in cur.fetchall():
        if pdf_filename:
            base = Path(pdf_filename).name
            by_basename.setdefault(base, cid)
        if ckey:
            ckey_str = str(ckey)
            by_ckey.setdefault(ckey_str, cid)
            tokens_by_id[cid] = set(str(ckey_str).split())
        else:
            tokens_by_id[cid] = set()

    print(
        f"[INFO] Chroniken-Lookup geladen: "
        f"{len(by_basename)} via basename, {len(by_ckey)} via canonical_key"
    )
    return ChronikLookup(by_basename=by_basename, by_ckey=by_ckey, tokens_by_id=tokens_by_id)


def _guess_chronik_from_ckey(ckey_from: str, lookup: ChronikLookup) -> Optional[int]:
    """
    Fuzzy-Fallback: finde die Chronik mit maximalem Token-Overlap zu ckey_from.

    Sehr einfache Heuristik:
        - Tokens = canonical_key(...).split()
        - Score = |Tokens ∩ Tokens_chronik|
        - beste ID mit Score >= 1 wird genommen.
    """
    tokens = set(ckey_from.split())
    if not tokens or not lookup.tokens_by_id:
        return None

    best_id: Optional[int] = None
    best_score = 0
    for cid, ctoks in lookup.tokens_by_id.items():
        if not ctoks:
            continue
        score = len(tokens & ctoks)
        if score > best_score:
            best_score = score
            best_id = cid

    return best_id if best_score >= 1 else None


# ---------------------------------------------------------------------------
# chroniken_texts: Chronik ↔ Dokument/Edition (bibliography)
# ---------------------------------------------------------------------------

def _ensure_chroniken_texts_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS chroniken_texts (
            chronik_id      INTEGER NOT NULL,
            bibliography_id INTEGER NOT NULL,
            role            TEXT    NOT NULL,
            PRIMARY KEY (chronik_id, bibliography_id, role),
            FOREIGN KEY (chronik_id)      REFERENCES chroniken(id),
            FOREIGN KEY (bibliography_id) REFERENCES bibliography(id)
        );
        """
    )
    conn.commit()
    print("[INFO] Tabelle chroniken_texts sichergestellt.")


def _fill_chroniken_texts(conn: sqlite3.Connection, lookup: ChronikLookup) -> None:
    """
    Befüllt chroniken_texts auf Basis von:
      - chroniken.pdf_filename  → role='original_pdf'
      - bibliography.file_basename 'chr_*.txt' → role='chronik_txt'
        (Mapping über canonical_key bzw. Token-Overlap)
    """
    _ensure_chroniken_texts_schema(conn)
    cur = conn.cursor()

    # Bibliography laden
    cur.execute(
        "SELECT id, file_basename, file_relpath, canonical_key "
        "FROM bibliography;"
    )
    bib_rows = cur.fetchall()
    print(f"[INFO] bibliography-Zeilen für chroniken_texts: {len(bib_rows)}")

    # Index: basename → (bib_id, canonical_key)
    bib_by_basename: Dict[str, Tuple[int, str]] = {}
    for bid, base, rel, ckey in bib_rows:
        base_str = str(base or "")
        bib_by_basename.setdefault(base_str, (int(bid), str(ckey or "")))

    # 1) original_pdf-Zuordnungen
    inserted_pdf = 0
    if lookup.by_basename:
        for base, chronik_id in lookup.by_basename.items():
            # base ist pdf_filename-Basename aus chroniken
            bib_entry = bib_by_basename.get(base)
            if not bib_entry:
                continue
            bib_id, _ = bib_entry
            try:
                cur.execute(
                    """
                    INSERT OR IGNORE INTO chroniken_texts
                        (chronik_id, bibliography_id, role)
                    VALUES (?, ?, 'original_pdf');
                    """,
                    (chronik_id, bib_id),
                )
                if cur.rowcount:
                    inserted_pdf += 1
            except sqlite3.Error as e:
                print(
                    f"[ERROR] Insert original_pdf für Chronik {chronik_id} / "
                    f"Bib {bib_id} fehlgeschlagen: {e}"
                )
    print(f"[INFO] chroniken_texts: {inserted_pdf} 'original_pdf'-Zuordnungen eingefügt.")

    # 2) chr_*.txt → chronik_txt
    inserted_txt = 0

    for bid, base, rel, ckey in bib_rows:
        base_str = str(base or "")
        base_lower = base_str.lower()
        if not (base_lower.endswith(".txt") and base_lower.startswith("chr_")):
            continue

        # basename ohne 'chr_' + ohne Extension → Heuristik für Chronikname
        stem = Path(base_str).stem
        stem = re.sub(r"^chr[_\s]+", "", stem, flags=re.IGNORECASE)
        ckey_from = canonical_key(stem if stem else base_str)
        if not ckey_from:
            print(f"[INFO] chr-TXT ohne gültigen canonical_key: {base_str}")
            continue

        # direkter Lookup über canonical_key
        chronik_id = lookup.by_ckey.get(ckey_from)

        # Fuzzy-Fallback, falls direkter Lookup fehlschlägt
        if chronik_id is None:
            chronik_id = _guess_chronik_from_ckey(ckey_from, lookup)

        if chronik_id is None:
            print(f"[INFO] chr-TXT nicht zugeordnet: {base_str}")
            continue

        try:
            cur.execute(
                """
                INSERT OR IGNORE INTO chroniken_texts
                    (chronik_id, bibliography_id, role)
                VALUES (?, ?, 'chronik_txt');
                """,
                (chronik_id, int(bid)),
            )
            if cur.rowcount:
                inserted_txt += 1
                print(
                    f"[INFO] chr-TXT '{base_str}' → Chronik-ID {chronik_id} "
                    f"(role=chronik_txt)"
                )
        except sqlite3.Error as e:
            print(
                f"[ERROR] Insert chronik_txt für Chronik {chronik_id} / "
                f"Bib {bid} fehlgeschlagen: {e}"
            )

    conn.commit()
    print(f"[INFO] chroniken_texts: {inserted_txt} 'chronik_txt'-Zuordnungen eingefügt.")


# ---------------------------------------------------------------------------
# chroniken_canon_links: Chronik ↔ Canon-Eintrag
# ---------------------------------------------------------------------------

def _ensure_chroniken_canon_links_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS chroniken_canon_links (
            canon_id   INTEGER NOT NULL,
            chronik_id INTEGER NOT NULL,
            PRIMARY KEY (canon_id, chronik_id),
            FOREIGN KEY (canon_id)   REFERENCES chroniken_canon(id),
            FOREIGN KEY (chronik_id) REFERENCES chroniken(id)
        );
        """
    )
    conn.commit()
    print("[INFO] Tabelle chroniken_canon_links sichergestellt.")


def _fill_chroniken_canon_links(conn: sqlite3.Connection, lookup: ChronikLookup) -> None:
    """
    Verknüpft chroniken_canon mit chroniken über canonical_key/Fuzzy-Matching.

    Logik:
      - Nur Einträge kind ∈ {'work', 'series'} werden gemappt.
      - Name-Basis: canonical falls vorhanden, sonst label.
      - canonical_key(name) → chroniken.canonical_key, Fallback via Token-Overlap.
    """
    _ensure_chroniken_canon_links_schema(conn)
    cur = conn.cursor()

    try:
        cur.execute(
            "SELECT id, kind, canonical, label FROM chroniken_canon;"
        )
    except sqlite3.OperationalError as e:
        print(f"[WARN] Konnte Tabelle 'chroniken_canon' nicht lesen: {e}")
        return

    rows = cur.fetchall()
    print(f"[INFO] chroniken_canon-Einträge: {len(rows)}")

    inserted = 0
    for canon_id, kind, canonical, label in rows:
        kind_str = str(kind or "").lower()
        if kind_str not in {"work", "series"}:
            continue

        name = str(canonical or "") or str(label or "")
        if not name:
            continue

        ck = canonical_key(name)
        if not ck:
            continue

        chronik_id = lookup.by_ckey.get(ck)
        if chronik_id is None:
            chronik_id = _guess_chronik_from_ckey(ck, lookup)

        if chronik_id is None:
            continue

        try:
            cur.execute(
                """
                INSERT OR IGNORE INTO chroniken_canon_links
                    (canon_id, chronik_id)
                VALUES (?, ?);
                """,
                (int(canon_id), int(chronik_id)),
            )
            if cur.rowcount:
                inserted += 1
        except sqlite3.Error as e:
            print(
                f"[ERROR] Insert chroniken_canon_links für canon_id={canon_id} / "
                f"chronik_id={chronik_id} fehlgeschlagen: {e}"
            )

    conn.commit()
    print(f"[INFO] chroniken_canon_links: {inserted} Zuordnungen eingefügt.")


# ---------------------------------------------------------------------------
# Verarbeitung der Treffer (DF) in die DB – nur edges_cc
# ---------------------------------------------------------------------------

def _process_df_to_db(df, db_path: Path, root: Path) -> int:
    """Schreibt Treffer aus df in die Tabelle edges_cc.

    Logik:
        - run_finder() liefert Trefferzeilen mit u. a.:
              pdf_file / pdf_path, label, group, page, context
        - Dieses Skript:
            1. Verwendet ggf. einen externen SEARCH_RUN_ID oder erzeugt
               einen Eintrag in search_runs (kind='chronik_search').
            2. Stellt sicher:
                 - chroniken_texts ist gefüllt (Chronik ↔ Original-PDF / chr_*.txt).
                 - chroniken_canon_links ist gefüllt (Canon-Eintrag ↔ Chronik).
            3. Mappt jede Quell-Datei auf eine Chronik-ID (from_id):
                 - 1) exakter Match über pdf_filename-Basename
                 - 2) canonical_key(Basename oder Stem) → chroniken.canonical_key
                 - 3) Fuzzy-Overlap zwischen Tokens der canonical_keys
            4. Mappt jedes label (Chronik-Name) über canonical_key(label) auf eine
               Chronik-ID (to_id).
            5. Aggregiert Kanten (from_id, to_id) mit Gewicht = Anzahl Treffer
               und sammelt Beispiel-Kontexte (raw_source).
            6. Schreibt alles nach edges_cc.

    WICHTIG:
        - Es werden KEINE Einträge in bibliography / works_canon / authors angelegt.
        - Dieses Skript beschreibt nur das Chronik→Chronik-Netzwerk.
    """
    if df is None:
        return 0

    try:
        import pandas as pd  # type: ignore
    except Exception:
        return 0

    if not isinstance(df, pd.DataFrame) or df.empty:
        return 0

    processed_rows = 0

    with sqlite3.connect(str(db_path)) as conn:
        env_run = os.environ.get("SEARCH_RUN_ID")
        if env_run:
            try:
                run_id = int(env_run)
                print(f"[INFO] Nutze externen search_run.id={run_id} (SEARCH_RUN_ID).")
            except ValueError:
                print(f"[WARN] Ungültige SEARCH_RUN_ID='{env_run}', erzeuge neuen Lauf.")
                run_id = create_search_run(conn, kind="chronik_search", session_dir=None)
        else:
            run_id = create_search_run(conn, kind="chronik_search", session_dir=None)

        # Chroniken-Lookup laden (für cc-Kanten + Text/Canon-Zuordnung)
        chronik_lookup = _load_chronik_lookup(conn)

        # chroniken_texts füllen (Chronik ↔ TXT/PDF)
        _fill_chroniken_texts(conn, chronik_lookup)

        # chroniken_canon_links füllen (Chronik ↔ Canon-Eintrag)
        _fill_chroniken_canon_links(conn, chronik_lookup)

        by_basename = chronik_lookup.by_basename
        by_ckey = chronik_lookup.by_ckey

        # Akkumulator für CC-Kanten: (from_id, to_id) -> {"weight": float, "samples": [str]}
        cc_edges: Dict[Tuple[int, int], Dict[str, object]] = defaultdict(
            lambda: {"weight": 0.0, "samples": []}
        )

        # Damit Warnungen nicht tausendfach wiederholt werden:
        missing_source_warned: Set[str] = set()

        for _, row in df.iterrows():
            pdf_file = str(row.get("pdf_file") or row.get("pdf_path") or "").strip()
            label = str(row.get("label") or "").strip()
            if not pdf_file or not label:
                continue

            processed_rows += 1

            path = Path(pdf_file)
            basename = path.name
            suffix = path.suffix.lower()

            # Quelle ist nur interessant, wenn es sich um eine Chronik-Datei handelt
            # (Heuristik: beginnt mit 'chr_' oder ist eine .txt).
            is_chronik_source = basename.startswith("chr_") or suffix == ".txt"
            if not is_chronik_source:
                continue

            group = str(row.get("group") or "").strip().lower()
            # Nur "echte" Chroniken-Labels (z.B. work/series), generics ignorieren
            if group not in {"work", "series"}:
                continue

            # ------------------------------------------------------
            # 1) Quelle (from_id) bestimmen
            # ------------------------------------------------------
            from_id: Optional[int] = None

            # 1a) direkter Lookup über pdf_filename-Basename
            from_id = by_basename.get(basename)

            # 1b) canonical_key(Basename / Stem) → chroniken.canonical_key
            if from_id is None:
                stem = path.stem
                stem = re.sub(r"^chr[_\s]+", "", stem, flags=re.IGNORECASE)
                ckey_from = canonical_key(stem if stem else basename)
                if ckey_from:
                    from_id = by_ckey.get(ckey_from)

            # 1c) Fuzzy-Fallback via Token-Overlap
            if from_id is None:
                stem = path.stem
                stem = re.sub(r"^chr[_\s]+", "", stem, flags=re.IGNORECASE)
                ckey_from = canonical_key(stem if stem else basename)
                if ckey_from:
                    from_id = _guess_chronik_from_ckey(ckey_from, chronik_lookup)

            if from_id is None:
                if basename not in missing_source_warned:
                    print(f"[WARN] Keine Chronik-Zeile für Quelle '{basename}' gefunden.")
                    missing_source_warned.add(basename)
                continue

            # ------------------------------------------------------
            # 2) Ziel (to_id) über label bestimmen
            # ------------------------------------------------------
            ckey_to = canonical_key(label)
            if not ckey_to:
                continue
            to_id = by_ckey.get(ckey_to)
            if to_id is None:
                # Fuzzy-Fallback analog zur Quelle
                to_id = _guess_chronik_from_ckey(ckey_to, chronik_lookup)

            if to_id is None:
                continue

            # Selbst-Kanten ignorieren (optional)
            if from_id == to_id:
                continue

            # ------------------------------------------------------
            # 3) Kante akkumulieren
            # ------------------------------------------------------
            key = (from_id, to_id)
            entry = cc_edges[key]
            entry["weight"] = float(entry["weight"]) + 1.0

            # Beispiel-Kontext sammeln (max. 5 Samples pro Kante)
            samples: List[str] = entry["samples"]  # type: ignore[assignment]
            if len(samples) < 5:
                page = row.get("page")
                ctx = str(row.get("context") or "")
                ctx_short = ctx.replace("\n", " ")[:240]
                if page:
                    samples.append(f"p{page}: {ctx_short}")
                else:
                    samples.append(ctx_short)

        # ----------------------------------------------------------
        # 4) edges_cc in die DB schreiben
        # ----------------------------------------------------------
        if cc_edges:
            cur = conn.cursor()
            insert_sql = """
                INSERT INTO edges_cc (run_id, from_id, to_id, weight, raw_source)
                VALUES (?, ?, ?, ?, ?);
            """
            n_edges = 0
            for (from_id, to_id), info in cc_edges.items():
                weight = float(info["weight"])
                samples = info["samples"]  # type: ignore[assignment]
                raw_source = " || ".join(samples)
                cur.execute(insert_sql, (run_id, from_id, to_id, weight, raw_source))
                n_edges += 1
            print(f"[INFO] edges_cc: {n_edges} Kanten für Lauf {run_id} geschrieben.")
        else:
            print(f"[INFO] edges_cc: keine Kanten erzeugt (Lauf {run_id}).")

    return processed_rows


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    print("[INFO] chroniken_library-Finder startet…")
    # run_finder nutzt default pdf_dir und db_path (DB-Modus)
    session_dir, df, agg = run_finder()
    if session_dir is None or df is None:
        print("[ERROR] Lauf abgebrochen.")
        return

    # Projektwurzel und DB-Pfad ermitteln
    root = project_root()
    db_path = config_db_path(root)

    # Treffer direkt in DB schreiben (nur edges_cc + chroniken_texts + canon_links)
    count = _process_df_to_db(df, db_path, root)
    print(f"[INFO] {count} Trefferzeilen verarbeitet (edges_cc-Modus).")
    print(f"[INFO] Fertig. Session-Ordner: {session_dir}")


if __name__ == "__main__":
    main()
