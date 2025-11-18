# scripts/create_chronik_views.py
#!/usr/bin/env python3
"""
Erzeugt Views für ein Chroniken-Netz mit „schönen“ Labels.

Voraussetzungen (liegen bei dir bereits vor):
- chroniken(id, canonical_key, werk, pdf_filename, …)
- chroniken_canon(id, kind, canonical, label, patterns_json, weight)
- chroniken_canon_links(canon_id, chronik_id)
- edges_cc(run_id, from_id, to_id, weight, raw_source)

Ziel:
- v_cc_chronik_nodes:
    * ein Knoten pro Chronik
    * Label bevorzugt aus chroniken_canon.label,
      sonst Fallback auf chroniken.werk bzw. canonical_key
    * layer = 'chronik' (für Farbgebung im Frontend)

- v_cc_chronik_edges:
    * direkte Projektion von edges_cc
    * Spaltennamen passend für den Graph-Export (src_id, tgt_id, weight)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Pfad zur SQLite-DB; ggf. anpassen
DB_PATH = Path("config/chroniken.sqlite3")

SQL_DROP_VIEWS = [
    "DROP VIEW IF EXISTS v_cc_chronik_nodes;",
    "DROP VIEW IF EXISTS v_cc_chronik_edges;",
]

# Chronik-Knoten mit „sprechenden“ Labels
SQL_CREATE_V_CC_CHRONIK_NODES = r"""
CREATE VIEW v_cc_chronik_nodes AS
SELECT
    c.id AS id,
    -- Bevorzugt das Label aus chroniken_canon; Fallbacks auf Werk / canonical_key
    COALESCE(
        cc.label,
        c.werk,
        c.canonical_key,
        'Chronik #' || c.id
    ) AS label,
    'chronik' AS layer
FROM chroniken AS c
LEFT JOIN chroniken_canon_links AS l
       ON l.chronik_id = c.id
LEFT JOIN chroniken_canon AS cc
       ON cc.id = l.canon_id
       AND cc.kind IN ('work', 'series');
"""

# Kanten-View als dünne Hülle um edges_cc
SQL_CREATE_V_CC_CHRONIK_EDGES = r"""
CREATE VIEW v_cc_chronik_edges AS
SELECT
    run_id,
    from_id AS src_id,
    to_id   AS tgt_id,
    weight,
    raw_source
FROM edges_cc;
"""


def ensure_views(conn: sqlite3.Connection) -> None:
    """
    Löscht ggf. vorhandene Versionen der Views und legt sie neu an.
    """
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON;")

    for stmt in SQL_DROP_VIEWS:
        cur.execute(stmt)

    cur.execute(SQL_CREATE_V_CC_CHRONIK_NODES)
    cur.execute(SQL_CREATE_V_CC_CHRONIK_EDGES)

    conn.commit()


def main(DB_PATH=DB_PATH) -> None:


    print(f"[INFO] Öffne DB: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    try:
        ensure_views(conn)

        cur = conn.cursor()
        # kleine Kontrolle: wie viele Knoten / Kanten?
        cur.execute("SELECT COUNT(*) FROM v_cc_chronik_nodes;")
        n_nodes = cur.fetchone()[0]
        print(f"[INFO] v_cc_chronik_nodes – Anzahl Chroniken: {n_nodes}")

        cur.execute(
            "SELECT run_id, COUNT(*) FROM v_cc_chronik_edges "
            "GROUP BY run_id ORDER BY run_id;"
        )
        rows = cur.fetchall()
        if rows:
            print("[INFO] v_cc_chronik_edges – Kanten pro run_id:")
            for run_id, n in rows:
                print(f"    run_id={run_id}: {n} Kanten")
        else:
            print("[WARN] v_cc_chronik_edges ist leer – sind edges_cc gefüllt?")
    finally:
        conn.close()
        print("[INFO] Views v_cc_chronik_nodes und v_cc_chronik_edges aktualisiert.")


if __name__ == "__main__":
    main()