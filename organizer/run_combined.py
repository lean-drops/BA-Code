# run_combined_search.py
#!/usr/bin/env python3
"""
Startet chronik-search.py und authors_search.py mit derselben run_id
in der Tabelle search_runs.

Ablauf:
  1) Öffnet config/chroniken.sqlite3.
  2) Legt EINEN Eintrag in search_runs an (kind='combined_search').
  3) Setzt die Umgebungsvariable SEARCH_RUN_ID auf diesen Wert.
  4) Ruft chronik-search.py und authors_search.py nacheinander auf.

Voraussetzung:
  - chronik-search.py und authors_search.py lesen SEARCH_RUN_ID und
    verwenden diese ID anstelle eines eigenen create_search_run-Aufrufs.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

from organizer.authors.db_io import create_search_run


def main() -> None:
    here = Path(__file__).resolve().parent
    db_path = Path(r"/Users/python/PycharmProjects/BA-Code/config/chroniken.sqlite3")

    if not db_path.exists():
        raise FileNotFoundError(f"DB nicht gefunden: {db_path}")

    # 1) Einen gemeinsamen Run anlegen
    with sqlite3.connect(str(db_path)) as conn:
        run_id = create_search_run(conn, kind="combined_search", session_dir=None)
    print(f"[INFO] Gemeinsamer search_run angelegt: id={run_id}")

    # 2) Env erweitern
    env = os.environ.copy()
    env["SEARCH_RUN_ID"] = str(run_id)

    # 3) Pfade zu den Skripten
    chronik_script = here /"chronik"/ "chronik-search.py"
    authors_script = here / "authors"/ "authors_search.py"

    if not chronik_script.exists():
        raise FileNotFoundError(f"chronik-search.py nicht gefunden: {chronik_script}")
    if not authors_script.exists():
        raise FileNotFoundError(f"authors_search.py nicht gefunden: {authors_script}")

    # 4) Beide Skripte nacheinander starten (teilen sich denselben run_id)
    py = os.environ.get("PYTHON", None) or os.sys.executable

    print("[INFO] Starte chronik-search.py …")
    subprocess.run([py, str(chronik_script)], env=env, check=True)

    print("[INFO] Starte authors_search.py …")
    subprocess.run([py, str(authors_script)], env=env, check=True)

    print(f"[INFO] combined_search abgeschlossen. run_id={run_id}")


if __name__ == "__main__":
    main()