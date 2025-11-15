#!/usr/bin/env python3
"""
build_chroniken_db_v4.py

Erzeugt eine frische SQLite-Datenbank für:

- Chroniken (aus chroniken_canon.csv + chroniken_canon.json)
- Autoren-Kanon (aus authors_canon.json, inkl. Regex-Patterns)
- Werke-Kanon (aus works_canon.json, inkl. Regex-Patterns)
- Bibliographie-PDFs (aus data/azk_library, gemappt auf Autoren und Werke)
- Metadaten und Edge-Tabellen (ac/aa/cc) + search_runs
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from organizer.chronik.create_chronik_db import list_pdfs, compile_aliases, insert_chroniken

# ---------------------------------------------------------------------------
# Feste Pfade
# ---------------------------------------------------------------------------

PROJECT = Path("/Users/programming/PycharmProjects/Find_Bibliography_NEw")
CONFIG_DIR = PROJECT / "config"

# Chroniken
CSV_PATH = CONFIG_DIR / "chroniken_canon.csv"
JSON_PATH = CONFIG_DIR / "chroniken_canon.json"
PDF_DIR_CHRONIKEN = PROJECT / "data" / "chroniken_library" / "pdf"

# Sekundärliteratur
AUTHORS_JSON_PATH = CONFIG_DIR / "authors_canon.json"
WORKS_JSON_PATH = CONFIG_DIR / "works_canon.json"
PDF_DIR_SECONDARY = PROJECT / "data" / "azk_library"

# DB
DB_PATH = CONFIG_DIR / "chroniken.sqlite3"

# ---------------------------------------------------------------------------
# Normalisierung & Utility
# ---------------------------------------------------------------------------

STOPWORDS = {
    "chronik", "chronicon", "schweizerchronik", "geschichte", "geschichten",
    "berner", "zuercher", "zürcher", "luzerner", "eidgenossenschaft",
    "helveticum", "helvetica", "eidgenössische", "eidgenossische",
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


def tokenize(s: str) -> Set[str]:
    return {t for t in canonical_key(s).split() if len(t) >= 3}


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    A, B = set(a), set(b)
    if not A and not B:
        return 1.0
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def sql_ident(name: str) -> str:
    s = normalize_text(name)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    if not s:
        s = "col"
    if re.match(r"^\d", s):
        s = "c_" + s
    return s


# ---------------------------------------------------------------------------
# CSV-Vorreinigung und Parsing (Chroniken)
# ---------------------------------------------------------------------------

REPLACE_MAP = {
    "\u201C": '"',
    "\u201D": '"',
    "\u201E": '"',
    "\u00AB": '"',
    "\u00BB": '"',
    "\u2018": "'",
    "\u2019": "'",
}


def _sanitize_csv_text(raw: str) -> str:
    for k, v in REPLACE_MAP.items():
        raw = raw.replace(k, v)
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    return raw


def _sniff_delimiter(sample: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"])
        return dialect.delimiter
    except Exception:
        return ","


def read_csv_sanitized(path: Path) -> Tuple[List[Dict[str, str]], List[str]]:
    if not path.exists():
        raise FileNotFoundError(f"CSV fehlt: {path}")
    raw = path.read_text(encoding="utf-8-sig", errors="strict")
    sanitized = _sanitize_csv_text(raw)
    delimiter = _sniff_delimiter(sanitized[:4096])
    print(f"[DEBUG] CSV-Delimiter erkannt: '{delimiter}'")

    buf = io.StringIO(sanitized)
    reader = csv.reader(buf, delimiter=delimiter, quotechar='"', skipinitialspace=False)
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("CSV ist leer.")

    header = [h.strip() for h in header]
    if len(set(header)) != len(header):
        raise ValueError(f"Doppelte Spaltennamen im Header: {header}")

    rows: List[Dict[str, str]] = []
    for idx, row in enumerate(reader, start=2):
        if len(row) > len(header):
            extras = row[len(header):]
            row = row[:len(header)]
            try:
                notes_idx = header.index("Notizen")
            except ValueError:
                notes_idx = len(header) - 1
            row[notes_idx] = (row[notes_idx] or "")
            extra_text = " | EXTRA: " + " | ".join(x.strip() for x in extras if x.strip())
            row[notes_idx] = (row[notes_idx] + extra_text).strip()
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))
        if len(row) != len(header):
            raise ValueError(f"Zeile {idx}: Feldanzahl ungleich Header ({len(row)} vs {len(header)}).")
        rows.append({header[i]: row[i].strip() for i in range(len(header))})

    print(f"[DEBUG] CSV gelesen: {len(rows)} Zeilen, {len(header)} Spalten")
    return rows, header


def guess_canonical_field(fieldnames: List[str]) -> Optional[str]:
    candidates = ["Werk", "canonical", "titel", "chronik", "work", "title", "name"]
    fn_norm = {normalize_text(f): f for f in fieldnames}
    for c in candidates:
        if c.lower() in fn_norm:
            return fn_norm[c.lower()]
    return fieldnames[0] if fieldnames else None


# ---------------------------------------------------------------------------
# JSON laden (Chroniken + Autoren + Werke)
# ---------------------------------------------------------------------------

@dataclass
class JsonWork:
    raw_canonical: str
    canonical_key: str
    alias_patterns: List[str]


def load_json_works(json_path: Path) -> List[JsonWork]:
    if not json_path.exists():
        raise FileNotFoundError(f"JSON fehlt: {json_path}")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    out: List[JsonWork] = []
    for w in data.get("works", []):
        raw = (w.get("canonical") or "").strip()
        if not raw:
            continue
        aliases = [str(a) for a in (w.get("aliases") or [])]
        out.append(
            JsonWork(
                raw_canonical=raw,
                canonical_key=canonical_key(raw),
                alias_patterns=aliases,
            )
        )
    print(f"[DEBUG] JSON-Works (Chroniken) geladen: {len(out)}")
    return out


@dataclass
class ChronikenCanonRow:
    kind: str              # "work" | "series" | "generic"
    canonical: Optional[str]
    label: Optional[str]
    patterns: List[str]
    weight: float


def load_chroniken_canon(json_path: Path) -> List[ChronikenCanonRow]:
    if not json_path.exists():
        raise FileNotFoundError(f"JSON fehlt: {json_path}")
    data = json.loads(json_path.read_text(encoding="utf-8"))

    weights_map = data.get("weights") or {}

    def weight_for(kind: str) -> float:
        try:
            return float(weights_map.get(kind, 1.0))
        except Exception:
            return 1.0

    # Label aus canonical ableiten:
    # - alles vor der ersten "("
    # - außen Anführungszeichen, Leerzeichen und Kommata entfernen
    def derive_label_from_canonical(canonical: str) -> str:
        label = canonical
        paren_idx = label.find("(")
        if paren_idx != -1:
            label = label[:paren_idx]
        # außen trimmen
        label = label.strip()
        # führende/abschließende Anführungszeichen entfernen
        if label.startswith('"') and label.endswith('"') and len(label) >= 2:
            label = label[1:-1].strip()
        # führende/abschließende Kommata/Leerzeichen entfernen
        label = label.strip(" ,")
        return label

    rows: List[ChronikenCanonRow] = []

    # works: label aus canonical ableiten
    for w in data.get("works", []):
        canonical = (w.get("canonical") or "").strip()
        if not canonical:
            continue
        aliases = [str(a) for a in (w.get("aliases") or [])]
        label = derive_label_from_canonical(canonical)
        rows.append(
            ChronikenCanonRow(
                kind="work",
                canonical=canonical,
                label=label or None,
                patterns=aliases,
                weight=weight_for("work"),
            )
        )

    # series: label aus canonical ableiten
    for s in data.get("series", []):
        canonical = (s.get("canonical") or "").strip()
        if not canonical:
            continue
        aliases = [str(a) for a in (s.get("aliases") or [])]
        label = derive_label_from_canonical(canonical)
        rows.append(
            ChronikenCanonRow(
                kind="series",
                canonical=canonical,
                label=label or None,
                patterns=aliases,
                weight=weight_for("series"),
            )
        )

    # generic_terms: Label kommt direkt aus JSON (nicht aus canonical)
    for g in data.get("generic_terms", []):
        label = (g.get("label") or "").strip()
        if not label:
            continue
        patterns = [str(p) for p in (g.get("patterns") or [])]
        rows.append(
            ChronikenCanonRow(
                kind="generic",
                canonical=None,
                label=label,
                patterns=patterns,
                weight=weight_for("generic"),
            )
        )

    print(f"[DEBUG] Chroniken-Canon-Einträge geladen: {len(rows)}")
    return rows

@dataclass
class AuthorEntry:
    canonical_id: str
    display: Optional[str]
    filename_aliases: List[str]
    text_aliases: List[str]
    negatives: List[str]
    merge_into: Optional[str]


@dataclass
class AuthorsConfig:
    schema: Optional[str]
    notes: Optional[str]
    year_pattern: Optional[str]
    weights_json: str


def load_authors_canon(json_path: Path) -> Tuple[AuthorsConfig, List[AuthorEntry]]:
    if not json_path.exists():
        raise FileNotFoundError(f"authors_canon.json fehlt: {json_path}")

    data = json.loads(json_path.read_text(encoding="utf-8"))

    schema = data.get("schema")
    notes = data.get("notes")
    year_pattern = data.get("year_pattern")
    weights = data.get("weights") or {}
    weights_json = json.dumps(weights, ensure_ascii=False)

    cfg = AuthorsConfig(
        schema=schema,
        notes=notes,
        year_pattern=year_pattern,
        weights_json=weights_json,
    )

    authors: List[AuthorEntry] = []
    for a in data.get("authors", []):
        canonical_id = (a.get("canonical_id") or "").strip()
        if not canonical_id:
            continue

        display = a.get("display")
        filename_aliases = [str(p) for p in (a.get("filename_aliases") or [])]
        text_aliases = [str(p) for p in (a.get("text_aliases") or [])]
        negatives = [str(p) for p in (a.get("negatives") or [])]
        merge_into = a.get("merge_into")
        if merge_into:
            merge_into = merge_into.strip() or None

        authors.append(
            AuthorEntry(
                canonical_id=canonical_id,
                display=display,
                filename_aliases=filename_aliases,
                text_aliases=text_aliases,
                negatives=negatives,
                merge_into=merge_into,
            )
        )

    print(f"[DEBUG] Authors in authors_canon.json geladen: {len(authors)}")
    return cfg, authors


@dataclass
class WorkCanonEntry:
    canonical: str
    author: Optional[str]
    year: Optional[int]
    aliases: List[str]
    negatives: List[str]


@dataclass
class WorksCanonConfig:
    weights_json: str


def load_works_canon(json_path: Path) -> Tuple[WorksCanonConfig, List[WorkCanonEntry]]:
    if not json_path.exists():
        raise FileNotFoundError(f"works_canon.json fehlt: {json_path}")

    data = json.loads(json_path.read_text(encoding="utf-8"))

    weights = data.get("weights") or {}
    weights_json = json.dumps(weights, ensure_ascii=False)
    cfg = WorksCanonConfig(weights_json=weights_json)

    works: List[WorkCanonEntry] = []
    for w in data.get("works", []):
        canonical = (w.get("canonical") or "").strip()
        if not canonical:
            continue
        author = w.get("author")
        year_val: Optional[int] = None
        y = w.get("year")
        try:
            if y is not None:
                year_val = int(y)
        except Exception:
            year_val = None
        aliases = [str(a) for a in (w.get("aliases") or [])]
        negatives = [str(n) for n in (w.get("negatives") or [])]
        works.append(
            WorkCanonEntry(
                canonical=canonical,
                author=author,
                year=year_val,
                aliases=aliases,
                negatives=negatives,
            )
        )

    print(f"[DEBUG] Works in works_canon.json geladen: {len(works)}")
    return cfg, works


# ---------------------------------------------------------------------------
# DB-Helfer
# ---------------------------------------------------------------------------

def drop_all_user_objects(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys=OFF;")
    cur.execute("BEGIN;")
    for typ in ("view", "trigger", "table"):
        cur.execute(
            "SELECT name FROM sqlite_master "
            f"WHERE type='{typ}' AND name NOT LIKE 'sqlite_%';"
        )
        for (name,) in cur.fetchall():
            cur.execute(f'DROP {typ.upper()} IF EXISTS "{name}";')
    cur.execute("COMMIT;")
    cur.execute("PRAGMA foreign_keys=ON;")


def ensure_schema(conn: sqlite3.Connection, csv_fieldnames: List[str]) -> Dict[str, str]:
    """
    Erzeugt:
    - chroniken
    - chroniken_canon
    - authors_config, authors
    - works_canon_config, works_canon
    - bibliography, bibliography_authors, bibliography_works
    - search_runs, edges_ac, edges_aa, edges_cc
    """
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON;")

    # --- chroniken ---
    cols = []
    csv_to_sql: Dict[str, str] = {}
    for col in csv_fieldnames:
        sqlcol = sql_ident(col)
        csv_to_sql[col] = sqlcol
        cols.append(f'"{sqlcol}" TEXT')
    cols.extend(
        [
            '"canonical_key" TEXT',
            '"pdf_present" INTEGER NOT NULL DEFAULT 0',
            '"pdf_filename" TEXT',
            '"source_row_json" TEXT'
        ]
    )
    cur.execute(
        f"""
        CREATE TABLE chroniken (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            {", ".join(cols)}
        );
        """
    )
    cur.execute("CREATE INDEX idx_chroniken_ck ON chroniken(canonical_key);")

    # --- chroniken_canon (aus chroniken_canon.json) ---
    cur.execute(
        """
        CREATE TABLE chroniken_canon (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,            -- 'work' | 'series' | 'generic'
            canonical TEXT,
            label TEXT,
            patterns_json TEXT NOT NULL,
            weight REAL NOT NULL
        );
        """
    )
    cur.execute("CREATE INDEX idx_chroniken_canon_kind ON chroniken_canon(kind);")
    cur.execute("CREATE INDEX idx_chroniken_canon_canonical ON chroniken_canon(canonical);")
    cur.execute("CREATE INDEX idx_chroniken_canon_label ON chroniken_canon(label);")

    # --- authors_config ---
    cur.execute(
        """
        CREATE TABLE authors_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            schema TEXT,
            notes TEXT,
            year_pattern TEXT,
            weights_json TEXT
        );
        """
    )

    # --- authors (aus authors_canon.json) ---
    cur.execute(
        """
        CREATE TABLE authors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_id TEXT NOT NULL UNIQUE,
            display TEXT,
            filename_aliases_json TEXT NOT NULL,
            text_aliases_json TEXT NOT NULL,
            negatives_json TEXT NOT NULL,
            merge_into TEXT
        );
        """
    )
    cur.execute("CREATE INDEX idx_authors_display ON authors(display);")

    # --- works_canon_config ---
    cur.execute(
        """
        CREATE TABLE works_canon_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            weights_json TEXT NOT NULL
        );
        """
    )

    # --- works_canon ---
    cur.execute(
        """
        CREATE TABLE works_canon (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical TEXT NOT NULL UNIQUE,
            canonical_key TEXT NOT NULL,
            author TEXT,
            year INTEGER,
            aliases_json TEXT NOT NULL,
            negatives_json TEXT NOT NULL
        );
        """
    )
    cur.execute("CREATE INDEX idx_works_canon_ck ON works_canon(canonical_key);")

    # --- bibliography (PDF-Dateien aus azk_library) ---
    cur.execute(
        """
        CREATE TABLE bibliography (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_basename TEXT NOT NULL UNIQUE,
            file_relpath TEXT NOT NULL,
            canonical_key TEXT,
            pdf_present INTEGER NOT NULL DEFAULT 1
        );
        """
    )

    # --- bibliography_authors (n:m: PDF → Autoren) ---
    cur.execute(
        """
        CREATE TABLE bibliography_authors (
            bibliography_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL,
            PRIMARY KEY(bibliography_id, author_id),
            FOREIGN KEY(bibliography_id) REFERENCES bibliography(id),
            FOREIGN KEY(author_id) REFERENCES authors(id)
        );
        """
    )

    # --- bibliography_works (n:m: PDF → Werk-Kanon) ---
    cur.execute(
        """
        CREATE TABLE bibliography_works (
            bibliography_id INTEGER NOT NULL,
            work_id INTEGER NOT NULL,
            PRIMARY KEY(bibliography_id, work_id),
            FOREIGN KEY(bibliography_id) REFERENCES bibliography(id),
            FOREIGN KEY(work_id) REFERENCES works_canon(id)
        );
        """
    )

    # --- search_runs ---
    cur.execute(
        """
        CREATE TABLE search_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            started_at TEXT NOT NULL,
            session_dir TEXT,
            notes TEXT
        );
        """
    )

    # --- edges_ac (Sekundärwerk → Chronik) ---
    cur.execute(
        """
        CREATE TABLE edges_ac (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            bibliography_id INTEGER NOT NULL,
            chronik_id INTEGER NOT NULL,
            weight REAL,
            raw_source TEXT,
            FOREIGN KEY(run_id) REFERENCES search_runs(id),
            FOREIGN KEY(bibliography_id) REFERENCES bibliography(id),
            FOREIGN KEY(chronik_id) REFERENCES chroniken(id)
        );
        """
    )
    cur.execute("CREATE INDEX idx_edges_ac ON edges_ac(run_id);")

    # --- edges_aa (Sekundärwerk → Sekundärwerk) ---
    cur.execute(
        """
        CREATE TABLE edges_aa (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            from_bib_id INTEGER NOT NULL,
            to_bib_id INTEGER NOT NULL,
            weight REAL,
            raw_source TEXT,
            FOREIGN KEY(run_id) REFERENCES search_runs(id),
            FOREIGN KEY(from_bib_id) REFERENCES bibliography(id),
            FOREIGN KEY(to_bib_id) REFERENCES bibliography(id)
        );
        """
    )
    cur.execute("CREATE INDEX idx_edges_aa ON edges_aa(run_id);")

    # --- edges_cc (Chronik → Chronik) ---
    cur.execute(
        """
        CREATE TABLE edges_cc (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            from_id INTEGER NOT NULL,
            to_id INTEGER NOT NULL,
            weight REAL,
            raw_source TEXT,
            FOREIGN KEY(run_id) REFERENCES search_runs(id),
            FOREIGN KEY(from_id) REFERENCES chroniken(id),
            FOREIGN KEY(to_id) REFERENCES chroniken(id)
        );
        """
    )
    cur.execute("CREATE INDEX idx_edges_cc ON edges_cc(run_id);")

    conn.commit()
    return csv_to_sql


# ---------------------------------------------------------------------------
# Insert: Works Canon (aus works_canon.json)
# ---------------------------------------------------------------------------

def insert_works_canon(conn: sqlite3.Connection,
                       cfg: WorksCanonConfig,
                       works: List[WorkCanonEntry]) -> None:
    cur = conn.cursor()

    # Meta-Konfiguration
    cur.execute("""
        INSERT INTO works_canon_config (id, weights_json)
        VALUES (1, ?)
    """, (cfg.weights_json,))

    insert_sql = """
        INSERT INTO works_canon
            (canonical, canonical_key, author, year, aliases_json, negatives_json)
        VALUES (?, ?, ?, ?, ?, ?);
    """

    for w in works:
        cur.execute(insert_sql, (
            w.canonical,
            canonical_key(w.canonical),
            w.author,
            w.year,
            json.dumps(w.aliases, ensure_ascii=False),
            json.dumps(w.negatives, ensure_ascii=False),
        ))

    conn.commit()


def insert_authors(conn: sqlite3.Connection, cfg: AuthorsConfig, authors: List[AuthorEntry]) -> None:
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO authors_config (id, schema, notes, year_pattern, weights_json)
        VALUES (1, ?, ?, ?, ?);
        """,
        (cfg.schema, cfg.notes, cfg.year_pattern, cfg.weights_json),
    )

    insert_sql = """
        INSERT INTO authors
            (canonical_id, display, filename_aliases_json, text_aliases_json, negatives_json, merge_into)
        VALUES (?, ?, ?, ?, ?, ?);
    """

    for a in authors:
        cur.execute(
            insert_sql,
            (
                a.canonical_id,
                a.display,
                json.dumps(a.filename_aliases, ensure_ascii=False),
                json.dumps(a.text_aliases, ensure_ascii=False),
                json.dumps(a.negatives, ensure_ascii=False),
                a.merge_into,
            ),
        )

    conn.commit()


def insert_chroniken_canon(conn: sqlite3.Connection,
                           entries: List[ChronikenCanonRow]) -> None:
    cur = conn.cursor()
    insert_sql = """
        INSERT INTO chroniken_canon
            (kind, canonical, label, patterns_json, weight)
        VALUES (?, ?, ?, ?, ?);
    """
    for e in entries:
        cur.execute(
            insert_sql,
            (
                e.kind,
                e.canonical,
                e.label,
                json.dumps(e.patterns, ensure_ascii=False),
                e.weight,
            ),
        )
    conn.commit()
    print(f"[DEBUG] Chroniken-Canon in DB eingefügt: {len(entries)} Einträge")


# ---------------------------------------------------------------------------
# Insert: Bibliographie-Werke aus PDFs (mit Autoren- UND Werk-Matching)
# ---------------------------------------------------------------------------

def insert_bibliography(conn: sqlite3.Connection,
                        authors: List[AuthorEntry],
                        works_canon: List[WorkCanonEntry]) -> None:
    cur = conn.cursor()

    pdfs = list_pdfs(PDF_DIR_SECONDARY)
    print(f"[DEBUG] Bibliography-PDFs gefunden: {len(pdfs)}")

    # Mapping canonical_id → author_id
    cur.execute("SELECT id, canonical_id, display FROM authors;")
    author_id_by_canonical: Dict[str, int] = {}
    author_id_by_display_norm: Dict[str, int] = {}

    for aid, cid, disp in cur.fetchall():
        author_id_by_canonical[cid] = aid
        if disp:
            author_id_by_display_norm[normalize_text(disp)] = aid

    # Compile author regexes
    author_filename_patterns: Dict[str, List[re.Pattern]] = {
        a.canonical_id: compile_aliases(a.filename_aliases)
        for a in authors
        if a.filename_aliases
    }

    # Work-canon regex patterns
    work_alias_patterns: Dict[str, List[re.Pattern]] = {}
    work_negative_patterns: Dict[str, List[re.Pattern]] = {}

    for w in works_canon:
        work_alias_patterns[w.canonical] = compile_aliases(w.aliases)
        work_negative_patterns[w.canonical] = compile_aliases(w.negatives)

    # Load works_canon id mapping
    cur.execute("SELECT id, canonical FROM works_canon;")
    work_id_by_canonical = {canonical: wid for wid, canonical in cur.fetchall()}

    # Regex für "(Nachname, Vorname)"
    paren_name_re = re.compile(r"\(([^()]+)\)\.pdf$", re.IGNORECASE)

    insert_bib_sql = """
        INSERT INTO bibliography (file_basename, file_relpath, canonical_key, pdf_present)
        VALUES (?, ?, ?, 1);
    """
    insert_bib_author_sql = """
        INSERT OR IGNORE INTO bibliography_authors (bibliography_id, author_id)
        VALUES (?, ?);
    """
    insert_bib_work_sql = """
        INSERT OR IGNORE INTO bibliography_works (bibliography_id, work_id)
        VALUES (?, ?);
    """

    for pdf in pdfs:
        basename = pdf.name
        relpath = str(pdf.relative_to(PROJECT)) if PROJECT in pdf.parents else str(pdf)
        key = canonical_key(basename)

        author_matches: Set[int] = set()
        work_matches: Set[int] = set()

        # -------------------------------------------------------
        # 1) Author-Matching per "(Nachname, Vorname)"
        # -------------------------------------------------------
        m = paren_name_re.search(basename)
        if m:
            raw = normalize_text(m.group(1))
            if raw in author_id_by_display_norm:
                author_matches.add(author_id_by_display_norm[raw])

        # -------------------------------------------------------
        # 2) Author-Matching per filename-aliases (regex)
        # -------------------------------------------------------
        if not author_matches:
            path_str = str(pdf)
            for a in authors:
                regs = author_filename_patterns.get(a.canonical_id)
                if not regs:
                    continue
                if any(r.search(path_str) for r in regs):
                    target = a.merge_into or a.canonical_id
                    if target in author_id_by_canonical:
                        author_matches.add(author_id_by_canonical[target])

        # -------------------------------------------------------
        # 3) Author-Matching per Token-Overlap
        # -------------------------------------------------------
        if not author_matches:
            fn_tokens = tokenize(basename)
            best_score = 0
            best_aid = None
            for a in authors:
                tokens = set()
                if a.display:
                    tokens |= tokenize(a.display)
                tokens |= tokenize(a.canonical_id.replace("-", " "))
                score = len(fn_tokens & tokens)
                if score > best_score:
                    best_score = score
                    best_aid = a
            if best_aid and best_score >= 1:
                target = best_aid.merge_into or best_aid.canonical_id
                if target in author_id_by_canonical:
                    author_matches.add(author_id_by_canonical[target])

        # -------------------------------------------------------
        # 4) WORK-Matching über Regex-Patterns (works_canon)
        # -------------------------------------------------------
        for w in works_canon:
            alias_regexes = work_alias_patterns.get(w.canonical, [])
            if any(r.search(basename) for r in alias_regexes):
                negs = work_negative_patterns.get(w.canonical, [])
                if any(r.search(basename) for r in negs):
                    continue
                wid = work_id_by_canonical.get(w.canonical)
                if wid:
                    work_matches.add(wid)

        # -------------------------------------------------------
        # 5) WORK-Matching per token overlap (wenn nötig)
        # -------------------------------------------------------
        if not work_matches:
            fn_tokens = tokenize(basename)
            best_score = 0
            best_wid = None
            for w in works_canon:
                toks = tokenize(w.canonical.replace("_", " "))
                score = len(fn_tokens & toks)
                if score > best_score:
                    best_score = score
                    best_wid = w
            if best_wid and best_score >= 1:
                wid = work_id_by_canonical.get(best_wid.canonical)
                if wid:
                    work_matches.add(wid)

        # -------------------------------------------------------
        # Bibliography row einfügen
        # -------------------------------------------------------
        cur.execute(insert_bib_sql, (basename, relpath, key))
        bib_id = cur.lastrowid

        # Autoren zuordnen
        for aid in author_matches:
            cur.execute(insert_bib_author_sql, (bib_id, aid))

        # Werke zuordnen
        for wid in work_matches:
            cur.execute(insert_bib_work_sql, (bib_id, wid))

    conn.commit()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    print("[DEBUG] Starte Aufbau der SQLite-DB (frischer Import)")
    print(f"[DEBUG] CSV (Chroniken):   {CSV_PATH}")
    print(f"[DEBUG] JSON (Chroniken):  {JSON_PATH}")
    print(f"[DEBUG] JSON (Authors):    {AUTHORS_JSON_PATH}")
    print(f"[DEBUG] JSON (Works):      {WORKS_JSON_PATH}")
    print(f"[DEBUG] PDFs Chroniken:    {PDF_DIR_CHRONIKEN}")
    print(f"[DEBUG] PDFs Sekundär:     {PDF_DIR_SECONDARY}")
    print(f"[DEBUG] DB:                {DB_PATH}")

    # Chroniken laden
    rows, fieldnames = read_csv_sanitized(CSV_PATH)
    canon_field = guess_canonical_field(fieldnames)
    json_works = load_json_works(JSON_PATH)
    chroniken_canon_entries = load_chroniken_canon(JSON_PATH)

    # Autoren laden
    authors_cfg, author_entries = load_authors_canon(AUTHORS_JSON_PATH)

    # Works Canon laden
    works_cfg, works_entries = load_works_canon(WORKS_JSON_PATH)

    # DB erstellen
    with sqlite3.connect(DB_PATH) as conn:
        drop_all_user_objects(conn)
        csv_sql_map = ensure_schema(conn, fieldnames)

        insert_chroniken(conn, rows, csv_sql_map, canon_field, json_works)
        insert_authors(conn, authors_cfg, author_entries)
        insert_works_canon(conn, works_cfg, works_entries)
        insert_chroniken_canon(conn, chroniken_canon_entries)
        insert_bibliography(conn, author_entries, works_entries)

    print("[DEBUG] Fertig!")


if __name__ == "__main__":
    main()