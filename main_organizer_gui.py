#!/usr/bin/env python3
"""
run_both_searches.py

Führt Autoren- und Chroniken-Suche auf demselben PDF-Ordner aus und kopiert
authors_edges.csv in einen gemeinsamen Zielordner.

Verwendung:
    python run_both_searches.py

Die Basis- und Zielordner sind relativ zum Projektwurzelverzeichnis
(konfigurierbar über die Konstanten unten).
"""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Tuple

from organizer.authors.authors_search import run_on_folder as run_authors
from organizer.chronik.chronik_finder.run import run_finder

# Projektwurzel: Datei liegt im Projekt-Root
PROJECT_ROOT = Path(__file__).resolve().parent

# PDF-Basisordner (für beide Suchen identisch)
BASE_PDF_DIR = PROJECT_ROOT / "data" / "test"

# Zielordner für zusammengeführte CSVs
DEST_CSV_DIR = PROJECT_ROOT / "data" / "test_csv"

AUTHORS_EDGES_NAME = "authors_edges.csv"


def _ensure_dir(path: Path) -> Path:
    """
    Stellt sicher, dass ein Zielverzeichnis existiert.

    Args:
        path: Zielverzeichnis.

    Returns:
        Das existierende (oder neu angelegte) Verzeichnis.
    """
    if not path.exists():
        print(f"[DEBUG] Creating directory: {path}")
        path.mkdir(parents=True, exist_ok=True)
    return path


def _copy_csv(src: Path, dest_dir: Path) -> Path:
    """
    Kopiert eine CSV-Datei in ein Zielverzeichnis.

    Args:
        src: Pfad zur Quell-CSV.
        dest_dir: Zielverzeichnis.

    Returns:
        Pfad zur kopierten Datei im Zielverzeichnis.

    Raises:
        FileNotFoundError: Falls die Quell-CSV nicht existiert.
    """
    if not src.is_file():
        raise FileNotFoundError(f"CSV not found: {src}")
    dest = dest_dir / src.name
    print(f"[DEBUG] Copying {src} -> {dest}")
    shutil.copy2(src, dest)
    return dest


def run_both(base_dir: Path, dest_dir: Path) -> Tuple[Path, Path | None]:
    """
    Führt Autoren- und Chronikensuche aus und kopiert die Authors-CSV.

    Args:
        base_dir: Ordner mit den PDFs, die ausgewertet werden sollen.
        dest_dir: Zielordner für die zusammengeführten CSV-Dateien.

    Returns:
        Tupel:
          (Pfad zur kopierten authors_edges.csv,
           Pfad zur Chronik-CSV oder None, falls keine erzeugt wird)
    """
    print(f"[INFO] Starting combined run for base folder: {base_dir}")
    dest_dir = _ensure_dir(dest_dir)

    # --- Autoren-Suche ---
    print("[INFO] Running authors_search.run_on_folder…")
    # run_authors soll den Session-Ordner als String zurückgeben
    authors_session = Path(run_authors(str(base_dir))).resolve()
    print(f"[DEBUG] authors_session={authors_session}")

    authors_edges = authors_session / AUTHORS_EDGES_NAME
    copied_authors = _copy_csv(authors_edges, dest_dir)

    # --- Chroniken-Suche ---
    print("[INFO] Running chronik_finder.run_finder…")
    # Wichtig: gleichen base_dir verwenden, nicht default_pdf_dir()
    session_dir, df, agg = run_finder(pdf_dir=base_dir)
    chronik_session = Path(session_dir).resolve()
    print(f"[DEBUG] chronik_session={chronik_session}")

    # Falls dein Chronik-Finder weiterhin chroniken_mentions.csv schreibt,
    # kannst du das hier kopieren. In der aktuellen DB-Variante wird diese
    # CSV nicht erzeugt, also lassen wir das optional:
    chronik_csv = chronik_session / "chroniken_mentions.csv"
    copied_chronik = None
    if chronik_csv.is_file():
        copied_chronik = _copy_csv(chronik_csv, dest_dir)
    else:
        print("[INFO] Keine chroniken_mentions.csv gefunden (DB-Modus?). Überspringe Kopie.")

    print(f"[INFO] Done. Copied authors_edges.csv into: {dest_dir}")
    return copied_authors, copied_chronik


def main() -> None:
    print(f"[DEBUG] BASE_PDF_DIR={BASE_PDF_DIR}")
    print(f"[DEBUG] DEST_CSV_DIR={DEST_CSV_DIR}")
    run_both(BASE_PDF_DIR, DEST_CSV_DIR)


if __name__ == "__main__":
    main()