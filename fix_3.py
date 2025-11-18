# scripts/fix_author_aliases.py
#!/usr/bin/env python3
"""
Heuristische Bereinigung der Autoren-Tabelle in config/chroniken.sqlite3.

Ziel:
- "Pseudo-Autoren" wie
    - "Alter Zürichkrige, Toggenburger Erbschaftskrieg (Illi, Martin)"
    - "Speich-Heinrich"
    - "Der Alte Zürichkrieg ... (Rigendinger, Fritz)"
  sollen nicht als eigene Knoten im Netzwerk auftauchen, sondern auf die
  eigentlichen Personen gemappt werden.

Vorgehen:
1. Lade alle Autoren aus der Tabelle `authors`.
2. Basis-Autoren = solche mit mindestens einem Alias (filename/text) ODER
   bereits sinnvoll gepflegt; Pseudo-Aliase = merge_into IS NULL und
   KEINE Aliase (filename_aliases_json == [] UND text_aliases_json == []).
3. Für jeden Pseudo-Alias:
   a) Wenn im display- oder canonical-String ein "(Nachname, Vorname)"
      vorkommt → versuche direkten Match auf display der Basis-Autoren.
   b) Sonst: Token-Overlap zwischen canonical/display und Basis-Autoren.
      Wenn genau ein Kandidat die höchste Überschneidung hat → merge_into
      entsprechend setzen.

Wichtig:
- Das Skript ändert NUR die Spalte `merge_into` in `authors`.
- Es fügt KEINE neuen Autoren hinzu, löscht nichts und fasst Aliase nicht an.
- Vorher ein Backup von config/chroniken.sqlite3 anlegen ist trotzdem sinnvoll.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Pfad zur SQLite-DB – bei Bedarf anpassen
DB_PATH = Path("config/chroniken.sqlite3")


# ---------------------------------------------------------------------------
# Hilfsfunktionen: Normalisierung & Tokens
# ---------------------------------------------------------------------------

def _strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def normalize_text(s: str) -> str:
    """
    Grobe Normalisierung:
    - Umlaute/Diakritika entfernen
    - Kleinbuchstaben
    - Mehrfach-Leerzeichen reduzieren
    """
    s = _strip_accents(s or "")
    s = s.lower()
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def canonical_tokens(s: str) -> Set[str]:
    """
    Tokens ähnlich wie in build_chroniken_db_v4.tokenize():
    - normalize_text
    - nur alphanumerisch
    - Tokens mit Länge >= 3
    """
    base = normalize_text(s)
    base = re.sub(r"[^a-z0-9]+", " ", base)
    toks = [t for t in base.split() if len(t) >= 3]
    return set(toks)


# ---------------------------------------------------------------------------
# Datenklassen
# ---------------------------------------------------------------------------

@dataclass
class AuthorRow:
    id: int
    canonical_id: str
    display: str
    filename_aliases: List[str]
    text_aliases: List[str]
    negatives: List[str]
    merge_into: Optional[str]


# ---------------------------------------------------------------------------
# Laden der Autoren aus der DB
# ---------------------------------------------------------------------------

def load_authors(conn: sqlite3.Connection) -> List[AuthorRow]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
          id,
          canonical_id,
          COALESCE(display, '') AS display,
          filename_aliases_json,
          text_aliases_json,
          negatives_json,
          merge_into
        FROM authors
        """
    )
    rows: List[AuthorRow] = []
    for (
        aid,
        canon_id,
        disp,
        fa_json,
        ta_json,
        neg_json,
        merge_into,
    ) in cur.fetchall():
        try:
            fa = json.loads(fa_json) if fa_json else []
        except Exception:
            fa = []
        try:
            ta = json.loads(ta_json) if ta_json else []
        except Exception:
            ta = []
        try:
            neg = json.loads(neg_json) if neg_json else []
        except Exception:
            neg = []
        rows.append(
            AuthorRow(
                id=int(aid),
                canonical_id=str(canon_id or ""),
                display=str(disp or ""),
                filename_aliases=list(fa or []),
                text_aliases=list(ta or []),
                negatives=list(neg or []),
                merge_into=str(merge_into) if merge_into else None,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Erkennung von Basis-Autoren und Pseudo-Aliassen
# ---------------------------------------------------------------------------

def split_authors(authors: List[AuthorRow]) -> Tuple[List[AuthorRow], List[AuthorRow]]:
    """
    Basis-Autoren:
      - merge_into IS NULL
      - und mindestens ein Alias (filename/text) vorhanden

    Pseudo-Aliase:
      - merge_into IS NULL
      - und KEINE Aliase (filename/text)
    """
    base: List[AuthorRow] = []
    pseudo: List[AuthorRow] = []
    for a in authors:
        has_alias = bool(a.filename_aliases or a.text_aliases)
        if a.merge_into is None and has_alias:
            base.append(a)
        elif a.merge_into is None and not has_alias:
            pseudo.append(a)
        else:
            # bereits verlinkt (merge_into gesetzt) → als Basis ignorieren
            pass
    return base, pseudo


# ---------------------------------------------------------------------------
# Heuristiken für das Mapping
# ---------------------------------------------------------------------------

def build_display_index(base_authors: List[AuthorRow]) -> Dict[str, AuthorRow]:
    """
    Index: normalisierte Anzeigeform → Basis-Autor
    z.B. "illi, martin" → AuthorRow(illi-martin)
    """
    idx: Dict[str, AuthorRow] = {}
    for a in base_authors:
        key = normalize_text(a.display)
        if key and key not in idx:
            idx[key] = a
    return idx


def find_author_in_parens(s: str) -> Optional[str]:
    """
    Sucht am Ende des Strings nach " (...)" und gibt den Inhalt zurück,
    z.B. "Alter Zürichkrige ... (Illi, Martin)" → "Illi, Martin"
    """
    m = re.search(r"\(([^()]+)\)\s*$", s)
    if not m:
        return None
    return m.group(1).strip()


def map_pseudo_by_parens(pseudo: AuthorRow, display_index: Dict[str, AuthorRow]) -> Optional[AuthorRow]:
    """
    Nutzt den Teil in Klammern (Illi, Martin) um einen Basis-Autor zu finden.
    """
    # erst display, dann canonical_id probieren
    for source in (pseudo.display, pseudo.canonical_id):
        name_in_parens = find_author_in_parens(source)
        if not name_in_parens:
            continue
        key = normalize_text(name_in_parens)
        base = display_index.get(key)
        if base:
            return base
    return None


def map_pseudo_by_tokens(pseudo: AuthorRow, base_authors: List[AuthorRow]) -> Optional[AuthorRow]:
    """
    Fallback: Token-Overlap zwischen canonical_id/display des Pseudo-Eintrags
    und Basis-Autoren. Liefert nur dann ein Ergebnis, wenn genau EIN Basis-Autor
    den maximalen Score hat und Score >= 1.
    """
    pseudo_tokens = canonical_tokens(pseudo.canonical_id + " " + pseudo.display)
    if not pseudo_tokens:
        return None

    best_score = 0
    best_candidates: List[AuthorRow] = []

    for b in base_authors:
        btoks = canonical_tokens(b.canonical_id + " " + b.display)
        if not btoks:
            continue
        score = len(pseudo_tokens & btoks)
        if score > best_score:
            best_score = score
            best_candidates = [b]
        elif score == best_score and score > 0:
            best_candidates.append(b)

    if best_score < 1 or len(best_candidates) != 1:
        return None
    return best_candidates[0]


# ---------------------------------------------------------------------------
# Hauptlogik: merge_into setzen
# ---------------------------------------------------------------------------

def fix_author_aliases(conn: sqlite3.Connection) -> None:
    authors = load_authors(conn)
    base_authors, pseudo_authors = split_authors(authors)

    if not pseudo_authors:
        print("[INFO] Keine Pseudo-Aliase gefunden (alle Autoren haben Aliase oder merge_into).")
        return

    print(f"[INFO] Basis-Autoren: {len(base_authors)}")
    print(f"[INFO] Pseudo-Aliase (Kandidaten zum Verlinken): {len(pseudo_authors)}")

    display_index = build_display_index(base_authors)

    cur = conn.cursor()
    updated = 0
    skipped = 0

    for p in pseudo_authors:
        # 1) Klammern-Heuristik
        target = map_pseudo_by_parens(p, display_index)

        # 2) Fallback: Token-Overlap
        if not target:
            target = map_pseudo_by_tokens(p, base_authors)

        if not target:
            print(
                f"[WARN] Kein eindeutiger Basis-Autor für Pseudo-Eintrag id={p.id}, "
                f"canonical_id='{p.canonical_id}', display='{p.display}' gefunden."
            )
            skipped += 1
            continue

        # merge_into setzen
        print(
            f"[INFO] Setze merge_into für id={p.id} ('{p.display}') → "
            f"'{target.canonical_id}' (Display: '{target.display}')"
        )
        cur.execute(
            """
            UPDATE authors
            SET merge_into = ?
            WHERE id = ?
            """,
            (target.canonical_id, p.id),
        )
        updated += 1

    conn.commit()
    print(f"[INFO] Aktualisiert: merge_into bei {updated} Autoren gesetzt, {skipped} Einträge ohne eindeutiges Mapping.")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(DB_PATH=DB_PATH) -> None:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"SQLite-DB nicht gefunden: {DB_PATH}")

    print(f"[INFO] Öffne DB: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    try:
        fix_author_aliases(conn)
    finally:
        conn.close()
        print("[INFO] Fertig. Bitte bei Bedarf run_combined_search.py erneut ausführen.")


if __name__ == "__main__":
    main()