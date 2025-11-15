
Erzeugt eine frische SQLite-Datenbank für:

- Chroniken (aus chroniken_canon.csv + chroniken_canon.json)
- Autoren-Kanon (aus authors_canon.json, inkl. Regex-Patterns)
- Bibliographie-Werke (aus PDFs im azk_library-Ordner, gemappt auf Autoren)
- Metadaten und Edge-Tabellen (ac/aa/cc) + search_runs

WICHTIG:
- Die Tabellen works_json und secondary_works aus der vorherigen Version
  gibt es NICHT mehr.
- Statt authors_json heißt die Tabelle jetzt authors und enthält direkt
  die Patterns aus authors_canon.json.
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

# ---------------------------------------------------------------------------
# Feste Pfade
# ---------------------------------------------------------------------------

PROJECT = Path("/Users/programming/PycharmProjects/Find_Bibliography_NEw")
CONFIG_DIR = PROJECT / "config"

# Chroniken
CSV_PATH = CONFIG_DIR / "chroniken_canon.csv"
JSON_PATH = CONFIG_DIR / "chroniken_canon.json"
PDF_DIR_CHRONIKEN = PROJECT / "data" / "chroniken_library" / "pdf"

# Sekundärliteratur (ca. 30 Werke)
AUTHORS_JSON_PATH = CONFIG_DIR / "authors_canon.json"
PDF_DIR_SECONDARY = PROJECT / "data" / "azk_library"

# DB
DB_PATH = CONFIG_DIR / "chroniken.sqlite3"

# ---------------------------------------------------------------------------
# Normalisierung & Utility
# ---------------------------------------------------------------------------

STOPWORDS = {
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
# JSON laden (Chroniken + Autoren)
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
    - authors_config, authors
    - bibliography, bibliography_authors
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
            '"source_row_json" TEXT',
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
    cur.execute('CREATE INDEX idx_chroniken_canonical ON chroniken(canonical_key);')
    cur.execute('CREATE INDEX idx_chroniken_pdf ON chroniken(pdf_present);')

    # --- authors_config (Meta zu authors_canon.json) ---
    cur.execute(
        """
        CREATE TABLE authors_config (
            id           INTEGER PRIMARY KEY CHECK (id = 1),
            schema       TEXT,
            notes        TEXT,
            year_pattern TEXT,
            weights_json TEXT
        );
        """
    )

    # --- authors (direkt aus authors_canon.json) ---
    cur.execute(
        """
        CREATE TABLE authors (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_id          TEXT NOT NULL UNIQUE,
            display               TEXT,
            filename_aliases_json TEXT NOT NULL,
            text_aliases_json     TEXT NOT NULL,
            negatives_json        TEXT NOT NULL,
            merge_into            TEXT
        );
        """
    )
    cur.execute("CREATE INDEX idx_authors_canonical_id ON authors(canonical_id);")

    # --- bibliography (Sekundärwerke als PDF) ---
    cur.execute(
        """
        CREATE TABLE bibliography (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            file_basename TEXT NOT NULL UNIQUE,
            file_relpath  TEXT NOT NULL,
            canonical_key TEXT,
            pdf_present   INTEGER NOT NULL DEFAULT 1
        );
        """
    )

    # --- Zuordnung Bibliographie ↔ Autoren ---
    cur.execute(
        """
        CREATE TABLE bibliography_authors (
            bibliography_id INTEGER NOT NULL,
            author_id       INTEGER NOT NULL,
            PRIMARY KEY (bibliography_id, author_id),
            FOREIGN KEY(bibliography_id) REFERENCES bibliography(id),
            FOREIGN KEY(author_id)       REFERENCES authors(id)
        );
        """
    )

    # --- search_runs ---
    cur.execute(
        """
        CREATE TABLE search_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            kind        TEXT NOT NULL,
            started_at  TEXT NOT NULL,
            session_dir TEXT,
            notes       TEXT
        );
        """
    )

    # --- Edges: AC (Bibliographie → Chronik) ---
    cur.execute(
        """
        CREATE TABLE edges_ac (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id             INTEGER NOT NULL,
            bibliography_id    INTEGER NOT NULL,
            chronik_id         INTEGER NOT NULL,
            weight             REAL,
            raw_source         TEXT,
            FOREIGN KEY(run_id)          REFERENCES search_runs(id),
            FOREIGN KEY(bibliography_id) REFERENCES bibliography(id),
            FOREIGN KEY(chronik_id)      REFERENCES chroniken(id)
        );
        """
    )
    cur.execute("CREATE INDEX idx_edges_ac_run ON edges_ac(run_id);")

    # --- Edges: AA (Bibliographie → Bibliographie) ---
    cur.execute(
        """
        CREATE TABLE edges_aa (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id             INTEGER NOT NULL,
            from_bib_id        INTEGER NOT NULL,
            to_bib_id          INTEGER NOT NULL,
            weight             REAL,
            raw_source         TEXT,
            FOREIGN KEY(run_id)      REFERENCES search_runs(id),
            FOREIGN KEY(from_bib_id) REFERENCES bibliography(id),
            FOREIGN KEY(to_bib_id)   REFERENCES bibliography(id)
        );
        """
    )
    cur.execute("CREATE INDEX idx_edges_aa_run ON edges_aa(run_id);")

    # --- Edges: CC (Chronik → Chronik) ---
    cur.execute(
        """
        CREATE TABLE edges_cc (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id       INTEGER NOT NULL,
            from_id      INTEGER NOT NULL,
            to_id        INTEGER NOT NULL,
            weight       REAL,
            raw_source   TEXT,
            FOREIGN KEY(run_id)  REFERENCES search_runs(id),
            FOREIGN KEY(from_id) REFERENCES chroniken(id),
            FOREIGN KEY(to_id)   REFERENCES chroniken(id)
        );
        """
    )
    cur.execute("CREATE INDEX idx_edges_cc_run ON edges_cc(run_id);")

    conn.commit()
    return csv_to_sql


# ---------------------------------------------------------------------------
# PDF-Matching
# ---------------------------------------------------------------------------

def compile_aliases(patterns: List[str]) -> List[re.Pattern]:
    out: List[re.Pattern] = []
    for p in patterns:
        try:
            out.append(re.compile(p, re.IGNORECASE))
        except re.error:
            out.append(re.compile(re.escape(p), re.IGNORECASE))
    return out


def list_pdfs(pdf_dir: Path) -> List[Path]:
    if not pdf_dir.exists():
        return []
    return [p for p in pdf_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"]


def score_tokens_on_name(tokens: Set[str], name_norm: str) -> int:
    return sum(1 for t in tokens if t in name_norm)


# ---------------------------------------------------------------------------
# Insert + Binden: Chroniken
# ---------------------------------------------------------------------------

def insert_chroniken(
    conn: sqlite3.Connection,
    rows: List[Dict[str, str]],
    csv_to_sql: Dict[str, str],
    canon_field: str,
    json_works: List[JsonWork],
) -> None:
    cur = conn.cursor()

    pdfs = list_pdfs(PDF_DIR_CHRONIKEN)
    pdf_names = [p.name for p in pdfs]
    pdf_names_norm = [normalize_text(n) for n in pdf_names]
    assigned: Set[int] = set()

    alias_map: Dict[str, List[re.Pattern]] = {
        jw.canonical_key: compile_aliases(jw.alias_patterns) for jw in json_works
    }

    cols_part = ", ".join(f'"{v}"' for v in csv_to_sql.values())
    placeholders = ", ".join("?" for _ in range(len(csv_to_sql) + 4))
    insert_sql = (
        "INSERT INTO chroniken ("
        f"{cols_part}, canonical_key, pdf_present, pdf_filename, source_row_json"
        f") VALUES ({placeholders});"
    )

    for row in rows:
        source_json = json.dumps(row, ensure_ascii=False)
        title_val = row.get(canon_field, "") or ""
        ckey = canonical_key(title_val)

        matched_pdf: Optional[str] = None
        present = 0

        alias_regexes = alias_map.get(ckey, [])
        if alias_regexes and pdf_names:
            for i, (nm, nm_norm) in enumerate(zip(pdf_names, pdf_names_norm)):
                if i in assigned:
                    continue
                if any(r.search(nm) or r.search(nm_norm) for r in alias_regexes):
                    matched_pdf = nm
                    present = 1
                    assigned.add(i)
                    break

        if present == 0 and pdf_names:
            toks = {t for t in tokenize(title_val) if t not in STOPWORDS}
            best_i: Optional[int] = None
            best_score = 0
            for i, nm_norm in enumerate(pdf_names_norm):
                if i in assigned:
                    continue
                sc = score_tokens_on_name(toks, nm_norm)
                if sc > best_score:
                    best_score, best_i = sc, i
            if best_score >= 2 and best_i is not None:
                matched_pdf = pdf_names[best_i]
                present = 1
                assigned.add(best_i)

        values: List[str] = [row.get(csv_col, "") for csv_col in csv_to_sql.keys()]
        values.extend([ckey, present, matched_pdf, source_json])
        cur.execute(insert_sql, values)

    conn.commit()


# ---------------------------------------------------------------------------
# Insert: Authors (aus authors_canon.json)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Insert: Bibliographie-Werke aus PDFs + Mapping zu Autoren
# ---------------------------------------------------------------------------

def insert_bibliography(conn: sqlite3.Connection, authors: List[AuthorEntry]) -> None:
    """
    Liest alle PDFs im azk_library-Ordner, legt sie als 'bibliography'-Einträge an
    und verknüpft sie mit Autoren:
      1) Primär: Name im Dateinamen: '... (Nachname, Vorname).pdf'
      2) Fallback: filename_aliases aus authors_canon.json
    """
    cur = conn.cursor()

    pdfs = list_pdfs(PDF_DIR_SECONDARY)
    print(f"[DEBUG] Bibliography-PDFs gefunden: {len(pdfs)}")

    target_for_entry: Dict[str, str] = {}
    for a in authors:
        target_for_entry[a.canonical_id] = a.merge_into or a.canonical_id

    cur.execute("SELECT id, canonical_id, display FROM authors;")
    author_id_by_canonical: Dict[str, int] = {}
    author_id_by_display_norm: Dict[str, int] = {}
    for aid, canonical_id, display in cur.fetchall():
        author_id_by_canonical[canonical_id] = aid
        if display:
            disp_norm = normalize_text(display)
            author_id_by_display_norm[disp_norm] = aid

    filename_regexes: Dict[str, List[re.Pattern]] = {
        a.canonical_id: compile_aliases(a.filename_aliases)
        for a in authors
        if a.filename_aliases
    }

    name_in_parens_re = re.compile(r"\(([^()]+)\)\.pdf$", re.IGNORECASE)

    insert_bib_sql = """
        INSERT INTO bibliography (file_basename, file_relpath, canonical_key, pdf_present)
        VALUES (?, ?, ?, ?);
    """
    insert_link_sql = """
        INSERT OR IGNORE INTO bibliography_authors (bibliography_id, author_id)
        VALUES (?, ?);
    """

    for pdf in pdfs:
        path_str = str(pdf)
        try:
            relpath = str(pdf.relative_to(PROJECT))
        except ValueError:
            relpath = path_str

        basename = pdf.name
        key = canonical_key(basename)

        matched_author_ids: Set[int] = set()

        m = name_in_parens_re.search(basename)
        if m:
            raw_name = m.group(1).strip()
            norm_name = normalize_text(raw_name)
            aid = author_id_by_display_norm.get(norm_name)
            if aid is not None:
                matched_author_ids.add(aid)

        if not matched_author_ids and filename_regexes:
            for a in authors:
                regs = filename_regexes.get(a.canonical_id)
                if not regs:
                    continue
                if any(r.search(path_str) for r in regs):
                    target_cid = target_for_entry[a.canonical_id]
                    aid = author_id_by_canonical.get(target_cid)
                    if aid is not None:
                        matched_author_ids.add(aid)

        cur.execute(
            insert_bib_sql,
            (basename, relpath, key, 1),
        )
        bib_id = cur.lastrowid

        if not matched_author_ids:
            print(f"[DEBUG] Kein Author-Match für Bibliography-PDF: {basename}")
        else:
            for aid in matched_author_ids:
                cur.execute(insert_link_sql, (bib_id, aid))

    conn.commit()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    print("[DEBUG] Starte Aufbau der SQLite-DB (frischer Import)")
    print(f"[DEBUG] CSV (Chroniken):   {CSV_PATH}")
    print(f"[DEBUG] JSON (Chroniken):  {JSON_PATH}")
    print(f"[DEBUG] JSON (Authors):    {AUTHORS_JSON_PATH}")
    print(f"[DEBUG] PDFs Chroniken:    {PDF_DIR_CHRONIKEN}")
    print(f"[DEBUG] PDFs Sekundär:     {PDF_DIR_SECONDARY}")
    print(f"[DEBUG] DB:                {DB_PATH}")

    for p in (CSV_PATH, JSON_PATH, AUTHORS_JSON_PATH):
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))
            print(f"[DEBUG] {p.name} mtime: {ts}")
        except FileNotFoundError:
            pass

    rows, fieldnames = read_csv_sanitized(CSV_PATH)
    if not fieldnames:
        raise RuntimeError("CSV hat keine Spaltenüberschriften.")
    canon_field = guess_canonical_field(fieldnames)
    if not canon_field:
        raise RuntimeError("Konnte keine Titel-/Kanon-Spalte im CSV bestimmen.")
    print(f"[DEBUG] Titel/Kanon-Feld erkannt: '{canon_field}'")

    json_works = load_json_works(JSON_PATH)
    authors_cfg, author_entries = load_authors_canon(AUTHORS_JSON_PATH)

    with sqlite3.connect(DB_PATH) as conn:
        print("[DEBUG] Droppe bestehende Tabellen/Views/Trigger …")
        drop_all_user_objects(conn)

        print("[DEBUG] Erzeuge Schema …")
        csv_to_sql = ensure_schema(conn, fieldnames)

        print("[DEBUG] Fülle 'chroniken' …")
        insert_chroniken(conn, rows, csv_to_sql, canon_field, json_works)

        print("[DEBUG] Fülle 'authors' …")
        insert_authors(conn, authors_cfg, author_entries)

        print("[DEBUG] Fülle 'bibliography' + 'bibliography_authors' …")
        insert_bibliography(conn, author_entries)

    print("[DEBUG] Fertig.")
    print("Beispiele (im SQLite-Client):")
    print("  SELECT COUNT(*), SUM(pdf_present) FROM chroniken;")
    print("  SELECT COUNT(*) FROM authors;")
    print("  SELECT COUNT(*), SUM(pdf_present) FROM bibliography;")
    print(
        "  SELECT b.file_basename, a.display "
        "FROM bibliography b "
        "JOIN bibliography_authors ba ON ba.bibliography_id = b.id "
        "JOIN authors a ON a.id = ba.author_id "
        "ORDER BY b.file_basename;"
    )


if __name__ == "__main__":
    main()