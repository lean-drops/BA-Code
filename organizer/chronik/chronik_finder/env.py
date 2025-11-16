# organizer/chronik/chronik_finder/env.py
"""
Umgebungs- und Library-Detection für chronik_finder.

Stellt zur Verfügung:
- fitz        (PyMuPDF)            + HAVE_FITZ
- pandas als pd                    + HAVE_PANDAS
- networkx als nx                  + HAVE_NX
- pdfminer.six genutzt?            + HAVE_PDFMINER
"""

from __future__ import annotations

import sys

HAVE_FITZ = False
HAVE_PANDAS = False
HAVE_NX = False
HAVE_PDFMINER = False

fitz = None
pd = None
nx = None

# -------------------- PyMuPDF / fitz --------------------

try:
    import fitz as _fitz  # type: ignore

    fitz = _fitz
    HAVE_FITZ = True
    print(f"[DEBUG] PyMuPDF (fitz) OK: {_fitz.__file__}", file=sys.stderr)
except Exception as exc:  # pragma: no cover
    print(f"[WARN] PyMuPDF (fitz) nicht verfügbar: {exc}", file=sys.stderr)
    HAVE_FITZ = False

# -------------------- pandas --------------------

try:
    import pandas as _pd  # type: ignore

    pd = _pd
    HAVE_PANDAS = True
    print(f"[DEBUG] pandas OK: {_pd.__file__}", file=sys.stderr)
except Exception as exc:  # pragma: no cover
    pd = None
    HAVE_PANDAS = True
    print(
        f"[WARN] pandas nicht importierbar: {exc}. "
        f"Installiere im aktiven venv mit: pip install --force-reinstall pandas",
        file=sys.stderr,
    )

# -------------------- networkx --------------------

try:
    import networkx as _nx  # type: ignore

    nx = _nx
    HAVE_NX = True
    print(f"[DEBUG] networkx OK: {_nx.__file__}", file=sys.stderr)
except Exception as exc:  # pragma: no cover
    nx = None
    HAVE_NX = False
    print(
        f"[WARN] networkx nicht importierbar: {exc}. "
        f"Installiere im aktiven venv mit: pip install networkx",
        file=sys.stderr,
    )

# -------------------- pdfminer.six (optional) --------------------

try:
    # je nach Projektstruktur evtl. anders, das hier ist die Standard-Variante
    import pdfminer  # type: ignore

    HAVE_PDFMINER = True
    print(f"[DEBUG] pdfminer OK: {pdfminer.__file__}", file=sys.stderr)
except Exception as exc:  # pragma: no cover
    HAVE_PDFMINER = False
    print(
        f"[WARN] pdfminer.six nicht importierbar: {exc}. "
        f"OCR/Alttext wird dann nur über PyMuPDF erledigt.",
        file=sys.stderr,
    )