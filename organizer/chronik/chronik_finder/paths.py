# organizer/chronik/chronik_finder/paths.py
#!/usr/bin/env python3
"""
paths.py – Pfade und Projektstruktur für den Chroniken-Finder (DB-Version).

Funktionen:
- project_root(): Projektwurzel bestimmen.
- data_base_dir(root): Basispfad für Daten.
- default_pdf_dir(root): Standard-PDF-/Text-Ordner finden.
- config_db_path(root): Pfad zur SQLite-DB 'config/chroniken.sqlite3'.
- iter_pdfs(pdf_dir): rekursive PDF-/TXT-Iteration (rückwärtskompatibler Name).

Robustheit:
- Nutzt Umgebungsvariablen CHRONIK_ROOT, CHRONIK_DATA_DIR, CHRONIK_PDF_DIR wenn vorhanden.
- Fallbacks prüfen existierende Ordner.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

from .constants import DB_FILENAME


def _has_config_db(p: Path) -> bool:
    return (p / "config" / DB_FILENAME).exists()


def project_root() -> Path:
    # 1) Explizit gesetzt
    env_root = os.environ.get("CHRONIK_ROOT")
    if env_root:
        pr = Path(env_root).expanduser().resolve()
        if _has_config_db(pr):
            return pr

    # 2) Vom aktuellen Modul aus nach oben laufen
    here = Path(__file__).resolve()
    for p in [here] + list(here.parents):
        if (p / "config").is_dir() and _has_config_db(p):
            return p

    # 3) Arbeitsverzeichnis
    cwd = Path.cwd().resolve()
    if _has_config_db(cwd):
        return cwd

    # 4) Heuristik: Projektwurzel ist Ordner, der eine 'config'-Mappe hat
    for p in [cwd] + list(cwd.parents):
        if (p / "config").is_dir():
            return p

    return cwd


def data_base_dir(root: Path) -> Path:
    env = os.environ.get("CHRONIK_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return root / "data"


def default_pdf_dir(root: Path) -> Path:
    # 1) Umgebungsvariable priorisieren
    env = os.environ.get("CHRONIK_PDF_DIR")
    if env:
        return Path(env).expanduser().resolve()

    # 2) Bevorzugter Standard in diesem Projekt
    candidates = [
        data_base_dir(root) / "test"
        / data_base_dir(root) / "test",  # historischer Name (beibehalten)
        data_base_dir(root),             # Fallback
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def config_db_path(root: Path) -> Path:
    return root / "config" / DB_FILENAME


def iter_pdfs(pdf_dir: Path) -> Generator[Path, None, None]:
    """
    Iteriert rekursiv über alle unterstützten Dokumente.

    Historisch nur PDFs; jetzt auch .txt (Name beibehalten aus Kompatibilitätsgründen).
    """
    if not pdf_dir.exists():
        return

    # os.walk ist meist schneller als mehrfaches rglob
    exts = {".pdf", ".txt"}
    for root, dirs, files in os.walk(pdf_dir):
        root_path = Path(root)
        for fname in files:
            if Path(fname).suffix.lower() in exts:
                p = root_path / fname
                if p.is_file():
                    yield p


def _debug_print() -> None:
    r = project_root()
    d = data_base_dir(r)
    pdf = default_pdf_dir(r)
    db = config_db_path(r)
    print("[DEBUG] project_root    =", r)
    print("[DEBUG] data_base_dir   =", d)
    print("[DEBUG] default_pdf_dir =", pdf)
    print("[DEBUG] config_db_path  =", db)
    print("[DEBUG] doc_count       =", sum(1 for _ in iter_pdfs(pdf)) if pdf.exists() else 0)


def main() -> None:
    _debug_print()


if __name__ == "__main__":
    main()