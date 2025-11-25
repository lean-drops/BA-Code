# run_combined_search.py
#!/usr/bin/env python3
"""
Startet chronik-search.py und authors_search.py mit derselben run_id
in der Tabelle search_runs.

Ablauf:
  0) Führt optionale Fix-/Maintenance-Skripte aus (Views, chroniken_texts, Autoren-Aliasse).
  1) Öffnet config/chroniken.sqlite3.
  2) Legt EINEN Eintrag in search_runs an (kind='combined_search').
  3) Setzt die Umgebungsvariable SEARCH_RUN_ID auf diesen Wert.
  4) Startet chronik-search.py und authors_search.py parallel (getrennte Prozesse),
     beide teilen sich denselben run_id.

Voraussetzung:
  - chronik-search.py und authors_search.py lesen SEARCH_RUN_ID und
    verwenden diese ID anstelle eines eigenen create_search_run-Aufrufs.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

from organizer.authors.db_io import create_search_run

# Import der Fix-Skripte (liegen im gleichen Projekt-Root wie dieses Skript)
import fix_1  # create_chronik_views.py  (optional, siehe run_fixes)
import fix_2  # fill_chroniken_texts.py
import fix_3  # fix_author_aliases.py

db_path = Path(r"/Users/programming/PycharmProjects/BA-Codes/config/chroniken.sqlite3")


def run_fixes() -> None:
    """
    Führt die Fix-/Maintenance-Skripte aus.

    Annahme:
      - Alle Skripte verwenden intern dieselbe DB
        (z.B. config/chroniken.sqlite3 relativ zum Projekt-Root).
      - Sie sind idempotent bzw. können gefahrlos mehrfach laufen.
    """
    print("[INFO] Starte Fix-Skripte …")

    # Optional: Views / Konsistenzstrukturen (nur ausführen, wenn benötigt)
    # print("[INFO]   • create_chronik_views (fix_1)")
    # fix_1.main(db_path)

    # Join-Tabelle chroniken_texts füllen/aktualisieren
    print("[INFO]   • fill_chroniken_texts (fix_2)")
    fix_2.main(db_path)

    # Autoren-Aliasse bereinigen / merge_into setzen
    print("[INFO]   • fix_author_aliases (fix_3)")
    fix_3.main(db_path)

    print("[INFO] Fix-Skripte abgeschlossen.")


def _run_scripts_parallel(env: Dict[str, str], chronik_script: Path, authors_script: Path) -> None:
    """
    Startet chronik-search.py und authors_search.py parallel und wartet auf beide.

    Hebt im Fehlerfall die Exit-Codes hervor und bricht mit RuntimeError ab.
    """
    py = os.environ.get("PYTHON") or os.sys.executable

    scripts: List[Tuple[str, Path]] = [
        ("chronik-search.py", chronik_script),
        ("authors_search.py", authors_script),
    ]

    procs: List[Tuple[str, subprocess.Popen]] = []

    print("[INFO] Starte Scripts parallel …")
    for label, script in scripts:
        print(f"[INFO]   • {label} … ({script})")
        proc = subprocess.Popen([py, str(script)], env=env)
        procs.append((label, proc))

    exit_codes: Dict[str, int] = {}
    try:
        for label, proc in procs:
            rc = proc.wait()
            exit_codes[label] = rc
            print(f"[INFO] {label} beendet mit Exit-Code {rc}")
    finally:
        # Sicherstellen, dass kein Kind-Prozess hängen bleibt
        for _, proc in procs:
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass

    failed = [name for name, code in exit_codes.items() if code != 0]
    if failed:
        raise RuntimeError(f"Fehlerhafte Scripts: {', '.join(failed)}")


def main() -> None:
    here = Path(__file__).resolve().parent

    if not db_path.exists():
        raise FileNotFoundError(f"DB nicht gefunden: {db_path}")

    # 0) Konsistenz-Fixes ausführen, bevor ein neuer search_run angelegt wird
    run_fixes()

    # 1) Einen gemeinsamen Run anlegen
    with sqlite3.connect(str(db_path)) as conn:
        run_id = create_search_run(conn, kind="combined_search", session_dir=None)
    print(f"[INFO] Gemeinsamer search_run angelegt: id={run_id}")

    # 2) Env erweitern
    env = os.environ.copy()
    env["SEARCH_RUN_ID"] = str(run_id)

    # 3) Pfade zu den Skripten
    chronik_script = here / "chronik" / "chronik-search.py"
    authors_script = here / "authors" / "authors_search.py"

    if not chronik_script.exists():
        raise FileNotFoundError(f"chronik-search.py nicht gefunden: {chronik_script}")
    if not authors_script.exists():
        raise FileNotFoundError(f"authors_search.py nicht gefunden: {authors_script}")

    # 4) Beide Skripte parallel starten (teilen sich denselben run_id)
    _run_scripts_parallel(env, chronik_script, authors_script)

    print(f"[INFO] combined_search abgeschlossen. run_id={run_id}")


if __name__ == "__main__":
    main()