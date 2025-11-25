#!/usr/bin/env python3
"""
create_chroniken_db.py

Erzeugt eine frische SQLite-Datenbank aus:
- CSV:  /Users/programming/PycharmProjects/BA-Codes/config/chroniken_canon.csv
- JSON: /Users/programming/PycharmProjects/BA-Codes/config/chroniken_canon.json
und markiert, ob passende PDFs im Verzeichnis
/Users/programming/PycharmProjects/BA-Codes/data/chroniken_library/pdf vorhanden sind.

Fixes gegenüber älteren Versionen:
- CSV-Vorreinigung: typographische Anführungszeichen („ “ ‘ ’ « ») ⇒ normale ".
- Robustes Parsing (utf-8-sig, Delimiter-Autodetektion, Extra-Felder → "Notizen").
- Strenge Validierung: gleiche Spaltenanzahl, sonst präzise Fehlermeldung.
- Frischer Import: Alle Tabellen/Views/Trigger werden vorab gedroppt.
- PDF-Matching eindeutig: Eine PDF wird höchstens einem Datensatz zugewiesen.
- Matching-Pipeline: 1) JSON-Aliases (Regex) 2) Token-Score ≥ 2.

Tabellen:
- chroniken(id PK, <CSV-Spalten: TEXT>, canonical_key TEXT, pdf_present INTEGER, pdf_filename TEXT, source_row_json TEXT)
- works_json(json_canonical PK, canonical_key TEXT, aliases_json TEXT, matched_chronik_id INT FK, match_confidence REAL)

Usage:
- Datei ohne Argumente ausführen. Debug-Ausgaben erscheinen auf STDOUT.
"""

from __future__ import annotations
import csv
import io
import json
import re
import sqlite3
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Iterable, Set

# ---------- Feste Pfade ----------
PROJECT = Path("/Users/programming/PycharmProjects/BA-Codes")
CONFIG_DIR = PROJECT / "config"
CSV_PATH = CONFIG_DIR / "chroniken_canon.csv"
JSON_PATH = CONFIG_DIR / "chroniken_canon.json"
PDF_DIR = PROJECT / "data" / "chroniken_library" / "pdf"
DB_PATH = CONFIG_DIR / "chroniken.sqlite3"

# ---------- Normalisierung ----------
STOPWORDS = {
    "chronik", "chronicon", "schweizerchronik", "geschichte", "geschichten",
    "berner", "zuercher", "zürcher", "luzerner", "eidgenossenschaft",
    "helveticum", "helvetica", "eidgenössische", "eidgenossische"
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

# ---------- CSV Vorreinigung und Parsing ----------
REPLACE_MAP = {
    "\u201C": '"',  # “
    "\u201D": '"',  # ”
    "\u201E": '"',  # „
    "\u00AB": '"',  # «
    "\u00BB": '"',  # »
    "\u2018": "'",  # ‘
    "\u2019": "'",  # ’
}

def _sanitize_csv_text(raw: str) -> str:
    for k, v in REPLACE_MAP.items():
        raw = raw.replace(k, v)
    # vereinheitliche CRLF/LF
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    return raw

def _sniff_delimiter(sample: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"])
        return dialect.delimiter
    except Exception:
        # Fallback: Komma
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

    # Trim Header
    header = [h.strip() for h in header]
    if len(set(header)) != len(header):
        raise ValueError(f"Doppelte Spaltennamen im Header: {header}")

    rows: List[Dict[str, str]] = []
    for idx, row in enumerate(reader, start=2):
        # Falls zu viele Felder: hänge Extras an "Notizen" an
        if len(row) > len(header):
            extras = row[len(header):]
            row = row[:len(header)]
            try:
                notes_idx = header.index("Notizen")
            except ValueError:
                notes_idx = len(header) - 1  # letzte Spalte
            row[notes_idx] = (row[notes_idx] or "")
            extra_text = " | EXTRA: " + " | ".join(x.strip() for x in extras if x.strip())
            row[notes_idx] = (row[notes_idx] + extra_text).strip()
        # Falls zu wenige Felder: auffüllen
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

# ---------- JSON laden ----------
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
        out.append(JsonWork(raw_canonical=raw,
                            canonical_key=canonical_key(raw),
                            alias_patterns=aliases))
    print(f"[DEBUG] JSON-Works geladen: {len(out)}")
    return out

# ---------- DB-Helfer ----------
def drop_all_user_objects(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys=OFF;")
    cur.execute("BEGIN;")
    for typ in ("view", "trigger", "table"):
        cur.execute(f"SELECT name FROM sqlite_master WHERE type='{typ}' AND name NOT LIKE 'sqlite_%';")
        for (name,) in cur.fetchall():
            cur.execute(f'DROP {typ.upper()} IF EXISTS "{name}";')
    cur.execute("COMMIT;")
    cur.execute("PRAGMA foreign_keys=ON;")

def ensure_schema(conn: sqlite3.Connection, csv_fieldnames: List[str]) -> Dict[str, str]:
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON;")
    cols = []
    csv_to_sql: Dict[str, str] = {}
    for col in csv_fieldnames:
        sqlcol = sql_ident(col)
        csv_to_sql[col] = sqlcol
        cols.append(f'"{sqlcol}" TEXT')
    cols.extend([
        '"canonical_key" TEXT',
        '"pdf_present" INTEGER NOT NULL DEFAULT 0',
        '"pdf_filename" TEXT',
        '"source_row_json" TEXT'
    ])
    cur.execute(f"""
        CREATE TABLE chroniken (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            {", ".join(cols)}
        );
    """)
    cur.execute('CREATE INDEX idx_chroniken_canonical ON chroniken(canonical_key);')
    cur.execute('CREATE INDEX idx_chroniken_pdf ON chroniken(pdf_present);')
    cur.execute("""
        CREATE TABLE works_json (
            json_canonical TEXT PRIMARY KEY,
            canonical_key TEXT NOT NULL,
            aliases_json TEXT NOT NULL,
            matched_chronik_id INTEGER,
            match_confidence REAL,
            FOREIGN KEY(matched_chronik_id) REFERENCES chroniken(id)
        );
    """)
    cur.execute('CREATE INDEX idx_works_json_ck ON works_json(canonical_key);')
    conn.commit()
    return csv_to_sql

# ---------- PDF-Matching ----------
def compile_aliases(patterns: List[str]) -> List[re.Pattern]:
    out = []
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

# ---------- Insert + Binden ----------
def insert_chroniken(conn: sqlite3.Connection,
                     rows: List[Dict[str, str]],
                     csv_to_sql: Dict[str, str],
                     canon_field: str,
                     json_works: List[JsonWork]) -> None:
    cur = conn.cursor()
    pdfs = list_pdfs(PDF_DIR)
    pdf_names = [p.name for p in pdfs]
    pdf_names_norm = [normalize_text(n) for n in pdf_names]
    assigned: Set[int] = set()  # Indizes der bereits vergebenen PDFs

    # Aliases je canonical_key
    alias_map: Dict[str, List[re.Pattern]] = {
        jw.canonical_key: compile_aliases(jw.alias_patterns) for jw in json_works
    }

    insert_sql = f"""
        INSERT INTO chroniken ({", ".join([f'"{v}"' for v in csv_to_sql.values()])},
                               canonical_key, pdf_present, pdf_filename, source_row_json)
        VALUES ({", ".join(["?"] * (len(csv_to_sql) + 4))});
    """

    for row in rows:
        source_json = json.dumps(row, ensure_ascii=False)
        title_val = row.get(canon_field, "") or ""
        ckey = canonical_key(title_val)

        matched_pdf: Optional[str] = None
        present = 0

        # 1) Alias-Regex
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

        # 2) Token-Score-Fallback (≥2)
        if present == 0 and pdf_names:
            toks = {t for t in tokenize(title_val) if t not in STOPWORDS}
            best_i, best_score = None, 0
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

        values = [row.get(csv_col, "") for csv_col in csv_to_sql.keys()]
        values.extend([ckey, present, matched_pdf, source_json])
        cur.execute(insert_sql, values)

    conn.commit()

def bind_works_json(conn: sqlite3.Connection,
                    json_works: List[JsonWork],
                    canon_field: str) -> None:
    cur = conn.cursor()
    cur.execute(f'SELECT id, canonical_key, "{sql_ident(canon_field)}" FROM chroniken;')
    crows = cur.fetchall()
    key_to_row: Dict[str, Tuple[int, Set[str]]] = {}
    for rid, ckey, title in crows:
        key_to_row[ckey] = (rid, tokenize(title or ""))

    insert_sql = """
        INSERT INTO works_json (json_canonical, canonical_key, aliases_json, matched_chronik_id, match_confidence)
        VALUES (?, ?, ?, ?, ?);
    """

    for jw in json_works:
        matched_id: Optional[int] = None
        confidence: float = 0.0
        if jw.canonical_key in key_to_row:
            matched_id = key_to_row[jw.canonical_key][0]
            confidence = 1.0
        else:
            jw_tokens = tokenize(jw.raw_canonical)
            best_id, best_score = None, 0.0
            for _, (rid, toks) in key_to_row.items():
                sc = jaccard(jw_tokens, toks)
                if sc > best_score:
                    best_id, best_score = rid, sc
            matched_id, confidence = best_id, round(best_score, 4)

        cur.execute(insert_sql, (
            jw.raw_canonical,
            jw.canonical_key,
            json.dumps(jw.alias_patterns, ensure_ascii=False),
            matched_id,
            confidence
        ))
    conn.commit()

# ---------- main ----------
def main() -> None:
    print("[DEBUG] Starte Aufbau der SQLite-DB (frischer Import)")
    print(f"[DEBUG] CSV:   {CSV_PATH}")
    print(f"[DEBUG] JSON:  {JSON_PATH}")
    print(f"[DEBUG] PDFs:  {PDF_DIR}")
    print(f"[DEBUG] DB:    {DB_PATH}")

    # Zeitstempel zur Kontrolle
    for p in (CSV_PATH, JSON_PATH):
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))
            print(f"[DEBUG] {p.name} mtime: {ts}")
        except FileNotFoundError:
            pass

    # CSV lesen (sanitisiert)
    rows, fieldnames = read_csv_sanitized(CSV_PATH)
    if not fieldnames:
        raise RuntimeError("CSV hat keine Spaltenüberschriften.")
    canon_field = guess_canonical_field(fieldnames)
    if not canon_field:
        raise RuntimeError("Konnte keine Titel-/Kanon-Spalte im CSV bestimmen.")
    print(f"[DEBUG] Titel/Kanon-Feld erkannt: '{canon_field}'")

    # JSON laden
    json_works = load_json_works(JSON_PATH)

    # DB frisch erzeugen
    with sqlite3.connect(DB_PATH) as conn:
        print("[DEBUG] Droppe bestehende Tabellen/Views/Trigger …")
        drop_all_user_objects(conn)
        print("[DEBUG] Erzeuge Schema …")
        csv_to_sql = ensure_schema(conn, fieldnames)
        print("[DEBUG] Fülle 'chroniken' …")
        insert_chroniken(conn, rows, csv_to_sql, canon_field, json_works)
        print("[DEBUG] Fülle 'works_json' …")
        bind_works_json(conn, json_works, canon_field)

    print("[DEBUG] Fertig.")
    print("Beispiele:")
    print("  SELECT COUNT(*), SUM(pdf_present) FROM chroniken;")
    print("  SELECT json_canonical, matched_chronik_id, match_confidence FROM works_json ORDER BY match_confidence DESC LIMIT 10;")

if __name__ == "__main__":
    main()