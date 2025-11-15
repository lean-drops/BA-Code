# organizer/chronik/chronik_finder/constants.py
#!/usr/bin/env python3
"""
Zentrale Konstanten und Standard-Parameter für den Chroniken-Finder.

Diese Version ist JSON-unabhängig. Sie arbeitet mit einer SQLite-DB:
<PROJECT_ROOT>/config/chroniken.sqlite3
"""
from __future__ import annotations

from typing import Dict, List

# --- Extraktion/Heuristik ---
TEXT_MIN_LEN: int = 120
SNIPPET_LEN: int = 160
BIB_HEADINGS: List[str] = [
    r"^\s*literatur\s*$",
    r"^\s*bibliographi[ea]\s*$",
    r"^\s*literaturverzeichnis\s*$",
    r"^\s*references\s*$",
    r"^\s*bibliography\s*$",
    r"^\s*quellen\s*(und\s*literatur)?\s*$",
]

# --- Laufsteuerung ---
DEFAULT_SKIP_BIBLIOGRAPHY: bool = True
MAX_WORKERS_DEFAULT: int = 0  # 0 = auto (min(CPU-1, 4))

# --- Cache ---
CACHE_REL_DIR: str = "data/cache/pages"

# --- Datenquellen ---
DB_FILENAME: str = "chroniken.sqlite3"  # unter <PROJECT_ROOT>/config/

# --- Default-Gewichte, falls DB nichts liefert ---
DEFAULT_WEIGHTS: Dict[str, float] = {"work": 1.0, "series": 1.0, "generic": 1.0}


