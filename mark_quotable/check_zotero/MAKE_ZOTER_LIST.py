# /Users/programming/PycharmProjects/BA-Codes/zotero_export_ba_azk.py
"""
Exportiert alle Items aus der Zotero-Collection "BA-AZK" in eine JSON-Datei mit:
    - key
    - creators_str
    - title

Voraussetzungen:
    pip install pyzotero python-dotenv
    .env im PROJECT_ROOT mit:
        ZOTERO_API_KEY
        ZOTERO_LIBRARY_ID
        ZOTERO_LIBRARY_TYPE   (z.B. 'user')
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv

try:
    from pyzotero import zotero as pyzotero
except Exception as e:  # pragma: no cover
    print("[ERROR] pyzotero konnte nicht importiert werden. Bitte installieren:")
    print("    pip install pyzotero")
    raise


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path("/Users/programming/PycharmProjects/BA-Codes")

# .env laden
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY", "").strip()
ZOTERO_LIBRARY_ID = os.getenv("ZOTERO_LIBRARY_ID", "").strip()
ZOTERO_LIBRARY_TYPE = os.getenv("ZOTERO_LIBRARY_TYPE", "user").strip() or "user"

# Name der Collection (Zotero-Ordner)
COLLECTION_NAME = "BA-AZK"

# Ausgabe-Datei mit der Item-Liste
OUTPUT_PATH = (
    PROJECT_ROOT
    / "mark_quotable"
    / "data"
    / "ba_azk_items.json"
)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------


def ensure_zotero_client() -> pyzotero.Zotero:
    if not (ZOTERO_API_KEY and ZOTERO_LIBRARY_ID):
        raise RuntimeError(
            "ZOTERO_API_KEY oder ZOTERO_LIBRARY_ID ist nicht gesetzt. "
            "Bitte .env im Projektroot prüfen."
        )
    try:
        return pyzotero.Zotero(
            ZOTERO_LIBRARY_ID,
            ZOTERO_LIBRARY_TYPE,
            ZOTERO_API_KEY,
        )
    except Exception as e:
        raise RuntimeError(f"Zotero-Initialisierung fehlgeschlagen: {e}") from e


def find_collection_key(zot: pyzotero.Zotero, collection_name: str) -> str:
    """
    Sucht in allen Collections nach einem Eintrag mit exakt passendem Namen
    und gibt dessen Collection-Key zurück.
    """
    # everything(...) holt alle Seiten durch
    all_colls: List[Dict[str, Any]] = zot.everything(zot.collections())

    for coll in all_colls:
        data = coll.get("data", {})
        name = data.get("name", "")
        key = data.get("key", "")
        if name == collection_name:
            return key

    raise RuntimeError(
        f'Keine Zotero-Collection mit Namen "{collection_name}" gefunden.'
    )


def format_creators(creators: List[Dict[str, Any]]) -> str:
    """
    Baut einen einfachen Autor:innen-String "Nachname, Vorname; Nachname, Vorname".
    """
    names: List[str] = []
    for c in creators:
        last = c.get("lastName", "")
        first = c.get("firstName", "")
        if last and first:
            names.append(f"{last}, {first}")
        elif last:
            names.append(last)
    return "; ".join(names)


def export_ba_azk_items() -> None:
    """
    Holt alle Items aus der Collection COLLECTION_NAME und speichert sie
    als JSON-Liste in OUTPUT_PATH mit Feldern:
        key, creators_str, title
    """
    zot = ensure_zotero_client()

    print(f'[INFO] Suche Collection "{COLLECTION_NAME}" …')
    coll_key = find_collection_key(zot, COLLECTION_NAME)
    print(f"[INFO] Collection-Key: {coll_key}")

    print("[INFO] Hole Items aus der Collection …")
    raw_items: List[Dict[str, Any]] = zot.everything(zot.items(collection=coll_key))

    result: List[Dict[str, Any]] = []
    for it in raw_items:
        data = it.get("data", {})
        key = data.get("key", "")
        title = data.get("title", "")
        creators = data.get("creators", []) or []

        creators_str = format_creators(creators)

        result.append(
            {
                "key": key,
                "creators_str": creators_str,
                "title": title,
            }
        )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[OK] {len(result)} Einträge nach {OUTPUT_PATH} geschrieben.")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    try:
        export_ba_azk_items()
    except Exception as e:  # pragma: no cover
        print("[ERROR]", e)


if __name__ == "__main__":
    main()