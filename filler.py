# scripts/fill_chroniken_texts.py
# !/usr/bin/env python3
"""
Füllt die Join-Tabelle chroniken_texts anhand von:
- chroniken (Primärchroniken)
- bibliography (Dokumente: PDF, TXT, virtuelle Authors)

Strategie:
- Rolle 'original_pdf':
    chronik.pdf_filename ↔ bibliography.file_basename (normalisiert)
- Rolle 'chronik_txt':
    alle bibliography.dateien mit Basename 'chr_*.txt'
    → heuristische Zuordnung zu chroniken über Token-Overlap
      (canonical_key-basiert, inkl. einfache Stopword-Filter).

Konfiguration:
    DB_PATH: Pfad zu chroniken.sqlite3

Vorausgesetzte Tabellen:
    chroniken(id, werk, canonical_key, pdf_filename, ...)
    bibliography(id, file_basename, file_relpath, canonical_key, ...)

Neue Tabelle (falls noch nicht vorhanden):
    chroniken_texts(
        chronik_id      INTEGER NOT NULL,
        bibliography_id INTEGER NOT NULL,
        role            TEXT    NOT NULL,
        PRIMARY KEY (chronik_id, bibliography_id, role),
        FOREIGN KEY (chronik_id)      REFERENCES chroniken(id),
        FOREIGN KEY (bibliography_id) REFERENCES bibliography(id)
    )
"""

from __future__ import annotations

import sqlite3
import unicodedata
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

# Pfad zur SQLite-DB
DB_PATH = Path("config/chroniken.sqlite3")

# ab welcher Token-Überschneidung TXT→Chronik gemappt wird
MIN_TOKEN_OVERLAP = 1

# zusätzliche Stopwörter, die für TXT-Mapping wenig sagen
TXT_STOPWORDS: Set[str] = {
    "chr", "chronik", "chroniken", "chronicon", "chronica",
    "die", "von", "und", "zu", "zum", "zur", "der", "des",
    "den", "dem", "ein", "eine", "einer", "eines", "einem",
    "alt", "alte", "alten", "chronikdes", "chronikder",
    "berner", "luzerner", "basler", "zürcher", "zurcher",
}


# ---------------------------------------------------------------------------
# Normalisierung & Tokenisierung (angelehnt an build_chroniken_db_v4)
# ---------------------------------------------------------------------------

def _strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def normalize_text(s: str) -> str:
    s = _strip_accents(s)
    s = s.lower()
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def canonical_key_like(s: str) -> str:
    """
    Leicht vereinfachte canonical_key-Funktion:
    - Normalisiert Unicode/Umlaute
    - filtert Nicht-Alphanumerisches
    - entfernt Stopwörter, mehrfaches Leerzeichen
    """
    base = normalize_text(s)
    base = re.sub(r"[^a-z0-9]+", " ", base)
    tokens = base.split()
    # TXT-spezifische Stopwords raus
    tokens = [t for t in tokens if t not in TXT_STOPWORDS]
    return " ".join(tokens)


def token_set(s: str) -> Set[str]:
    return set(t for t in canonical_key_like(s).split() if t)


# ---------------------------------------------------------------------------
# DB-Modelle
# ---------------------------------------------------------------------------

@dataclass
class ChronikRow:
    id: int
    werk: str
    canonical_key: str
    pdf_filename: Optional[str]


@dataclass
class BibRow:
    id: int
    file_basename: str
    file_relpath: str
    canonical_key: str


# ---------------------------------------------------------------------------
# Laden der Tabellen
# ---------------------------------------------------------------------------

def load_chroniken(conn: sqlite3.Connection) -> List[ChronikRow]:
    cur = conn.cursor()
    cur.execute(
        "SELECT id, werk, canonical_key, pdf_filename "
        "FROM chroniken;"
    )
    rows: List[ChronikRow] = []
    for cid, werk, ckey, pdf in cur.fetchall():
        rows.append(
            ChronikRow(
                id=int(cid),
                werk=str(werk or ""),
                canonical_key=str(ckey or ""),
                pdf_filename=str(pdf) if pdf else None,
            )
        )
    print(f"[INFO] chroniken geladen: {len(rows)}")
    return rows


def load_bibliography(conn: sqlite3.Connection) -> List[BibRow]:
    cur = conn.cursor()
    cur.execute(
        "SELECT id, file_basename, file_relpath, canonical_key "
        "FROM bibliography;"
    )
    rows: List[BibRow] = []
    for bid, base, rel, ckey in cur.fetchall():
        rows.append(
            BibRow(
                id=int(bid),
                file_basename=str(base or ""),
                file_relpath=str(rel or ""),
                canonical_key=str(ckey or ""),
            )
        )
    print(f"[INFO] bibliography geladen: {len(rows)}")
    return rows


# ---------------------------------------------------------------------------
# chroniken_texts-Tabelle sicherstellen
# ---------------------------------------------------------------------------

def ensure_chroniken_texts_schema(conn: sqlite3.Connection) -> None:
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


# ---------------------------------------------------------------------------
# Matching-Helfer
# ---------------------------------------------------------------------------

def _norm_basename_for_match(name: str) -> str:
    """
    Unicode-/Case-insensitive Vergleichsbasis für file_basename / pdf_filename.
    """
    name = _strip_accents(name)
    name = name.lower()
    name = name.strip()
    return name


def build_basename_index(bib_rows: List[BibRow]) -> Dict[str, BibRow]:
    """
    Map von normalisiertem file_basename → BibRow (erste gewonnene).
    """
    idx: Dict[str, BibRow] = {}
    for row in bib_rows:
        key = _norm_basename_for_match(row.file_basename)
        idx.setdefault(key, row)
    return idx


def build_chronik_token_index(chroniken: List[ChronikRow]) -> Dict[int, Set[str]]:
    out: Dict[int, Set[str]] = {}
    for c in chroniken:
        out[c.id] = token_set(c.canonical_key or c.werk)
    return out


def best_txt_match_for_bib(
        bib: BibRow,
        chroniken: List[ChronikRow],
        chronik_tokens: Dict[int, Set[str]],
) -> Optional[int]:
    """
    Heuristisches Matching einer chr_*.txt-Datei auf eine Chronik:
    - Tokens aus bib.canonical_key / file_basename (mit TXT_STOPWORDS-Filter)
    - Score = |Token-Intersection|
    - bei best_score < MIN_TOKEN_OVERLAP → None
    - bei Mehrdeutigkeit (mehrere mit gleichem best_score) → None
    """
    # nur 'chr_*.txt' berücksichtigen
    base = bib.file_basename
    if not base.lower().endswith(".txt"):
        return None
    if not base.lower().startswith("chr_"):
        return None

    # Tokens für diese TXT
    txt_tokens = token_set(bib.canonical_key or base)
    if not txt_tokens:
        return None

    best_id: Optional[int] = None
    best_score = 0
    tie = False

    for c in chroniken:
        ctoks = chronik_tokens.get(c.id, set())
        if not ctoks:
            continue
        score = len(txt_tokens & ctoks)
        if score > best_score:
            best_score = score
            best_id = c.id
            tie = False
        elif score == best_score and score > 0 and best_id is not None:
            # Mehr als ein Kandidat mit gleichem Score
            tie = True

    if best_score < MIN_TOKEN_OVERLAP:
        return None
    if tie:
        # lieber keine falsche Zuordnung erzwingen
        print(
            f"[WARN] chr-TXT '{bib.file_basename}' hat mehrfach gleich guten Match "
            f"(Score={best_score}); wird übersprungen."
        )
        return None
    return best_id


# ---------------------------------------------------------------------------
# Fülllogik
# ---------------------------------------------------------------------------

def fill_chroniken_texts(conn: sqlite3.Connection) -> None:
    ensure_chroniken_texts_schema(conn)

    chroniken = load_chroniken(conn)
    bib_rows = load_bibliography(conn)

    basename_index = build_basename_index(bib_rows)
    chronik_tokens = build_chronik_token_index(chroniken)

    cur = conn.cursor()

    # 1) PDFs aus chroniken.pdf_filename → role='original_pdf'
    inserted_pdf = 0
    for c in chroniken:
        if not c.pdf_filename:
            continue
        key = _norm_basename_for_match(c.pdf_filename)
        bib = basename_index.get(key)
        if not bib:
            continue
        try:
            cur.execute(
                """
                INSERT OR IGNORE INTO chroniken_texts
                    (chronik_id, bibliography_id, role)
                VALUES (?, ?, ?);
                """,
                (c.id, bib.id, "original_pdf"),
            )
            if cur.rowcount:
                inserted_pdf += 1
        except sqlite3.Error as e:
            print(f"[ERROR] Insert original_pdf für Chronik {c.id} / Bib {bib.id} fehlgeschlagen: {e}")
    print(f"[INFO] chroniken_texts: {inserted_pdf} 'original_pdf'-Zuordnungen eingefügt.")

    # 2) TXT-Dateien chr_*.txt → role='chronik_txt'
    inserted_txt = 0
    for bib in bib_rows:
        base_norm = bib.file_basename.lower()
        if not (base_norm.endswith(".txt") and base_norm.startswith("chr_")):
            continue

        chronik_id = best_txt_match_for_bib(bib, chroniken, chronik_tokens)
        if chronik_id is None:
            print(f"[INFO] chr-TXT nicht zugeordnet: {bib.file_basename}")
            continue

        try:
            cur.execute(
                """
                INSERT OR IGNORE INTO chroniken_texts
                    (chronik_id, bibliography_id, role)
                VALUES (?, ?, ?);
                """,
                (chronik_id, bib.id, "chronik_txt"),
            )
            if cur.rowcount:
                inserted_txt += 1
                print(
                    f"[INFO] chr-TXT '{bib.file_basename}' → Chronik-ID {chronik_id} "
                    f"(role=chronik_txt)"
                )
        except sqlite3.Error as e:
            print(f"[ERROR] Insert chronik_txt für Chronik {chronik_id} / Bib {bib.id} fehlgeschlagen: {e}")

    conn.commit()
    print(f"[INFO] chroniken_texts: {inserted_txt} 'chronik_txt'-Zuordnungen eingefügt.")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DB nicht gefunden: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    try:
        fill_chroniken_texts(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()