"""
Render Authors Report from template.

Dieses Skript lädt die aggregierten CSVs (nodes/edges) aus dem jüngsten
Session-Ordner unter data/authors_data, ersetzt Platzhalter im HTML-Template
(config/authors_report_template/authors_report.html) und schreibt
authors_report.html in denselben Session-Ordner.

Voraussetzungen: Python 3.9+, pandas
Usage:
    Einfach ausführen. Keine Argumente. Pfade sind im Skript konfiguriert.
"""
from __future__ import annotations

import os
import sys
import html
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Tuple, Optional

try:
    import pandas as pd
except Exception as e:
    raise RuntimeError("pandas ist erforderlich. Installation: pip install pandas") from e


# ---- Projektpfade (anpassen falls nötig) -------------------------------------
BASE_DIR = "/Users/programming/PycharmProjects/BA-Codes"
TEMPLATE_DIR = os.path.join(BASE_DIR, "config", "authors_report_template")
DATA_DIR = os.path.join(BASE_DIR, "data", "authors_data")

TEMPLATE_HTML = os.path.join(TEMPLATE_DIR, "authors_report.html")


# ---- Utilities ---------------------------------------------------------------
def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def esc(s: object) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def read_template(path: str) -> str:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Template nicht gefunden: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def find_latest_session_with_csv(root: str) -> Optional[str]:
    if not os.path.isdir(root):
        return None
    cands = []
    for name in os.listdir(root):
        p = os.path.join(root, name)
        if os.path.isdir(p) and name.startswith("session_"):
            edges = os.path.join(p, "authors_edges.csv")
            nodes = os.path.join(p, "authors_nodes.csv")
            if os.path.isfile(edges) and os.path.isfile(nodes):
                cands.append((os.path.getmtime(p), p))
    if not cands:
        return None
    cands.sort(key=lambda t: t[0], reverse=True)
    return cands[0][1]


def create_session(root: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(root, f"session_{ts}_render")
    os.makedirs(out, exist_ok=True)
    return out


def load_csvs(session_dir: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    nodes_path = os.path.join(session_dir, "authors_nodes.csv")
    edges_path = os.path.join(session_dir, "authors_edges.csv")
    if not os.path.isfile(nodes_path):
        raise FileNotFoundError(f"Fehlt: {nodes_path}")
    if not os.path.isfile(edges_path):
        raise FileNotFoundError(f"Fehlt: {edges_path}")
    df_nodes = pd.read_csv(nodes_path, sep=";")
    df_edges = pd.read_csv(edges_path, sep=";")
    return df_nodes, df_edges


def id_to_label_map(df_nodes: pd.DataFrame) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if {"author_id", "display"}.issubset(df_nodes.columns):
        for aid, disp in zip(df_nodes["author_id"].astype(str), df_nodes["display"].astype(str)):
            out[aid] = disp
    return out


def ensure_edge_columns(df_edges: pd.DataFrame) -> pd.DataFrame:
    df = df_edges.copy()
    for col in ["src", "tgt", "total", "bib_hits", "text_hits", "examples"]:
        if col not in df.columns:
            if col in ("total", "bib_hits", "text_hits"):
                df[col] = 0
            elif col == "examples":
                df[col] = ""
            else:
                df[col] = ""
    if "weighted" not in df.columns:
        # Fallback: gleicher Score wie im bestehenden Aggregator
        df["weighted"] = df["bib_hits"].astype(float) * 2.0 + df["text_hits"].astype(float) * 1.0
    return df


def compute_asset_rel(out_dir: str, template_dir: str) -> str:
    rel = os.path.relpath(template_dir, start=out_dir)
    # Normalize to forward slashes for href/src
    return rel.replace(os.sep, "/")


def build_top_rows(series: pd.Series) -> str:
    # series: index=name, value=score
    parts = []
    for name, val in series.items():
        parts.append(f"<tr><td>{esc(name)}</td><td>{int(val)}</td></tr>")
    return "\n".join(parts)


def build_edges_rows(df_out: pd.DataFrame) -> str:
    parts = []
    for _, r in df_out.iterrows():
        parts.append(
            "<tr>"
            f"<td>{esc(r.get('src_label',''))}</td>"
            f"<td>{esc(r.get('tgt_label',''))}</td>"
            f"<td>{int(r.get('total',0))}</td>"
            f"<td>{int(r.get('bib_hits',0))}</td>"
            f"<td>{int(r.get('text_hits',0))}</td>"
            f"<td>{esc(r.get('examples',''))}</td>"
            "</tr>"
        )
    return "\n".join(parts)


def render_report_html(
    df_nodes: pd.DataFrame,
    df_edges: pd.DataFrame,
    template_html: str,
    out_dir: str,
    template_dir: str,
) -> str:
    labels = id_to_label_map(df_nodes)
    df = ensure_edge_columns(df_edges)

    # Labels
    df["src_label"] = df["src"].map(lambda x: labels.get(str(x), str(x)))
    df["tgt_label"] = df["tgt"].map(lambda x: labels.get(str(x), str(x)))

    # Top-Tabellen
    top_citers = (
        df.groupby("src_label")["weighted"].sum().sort_values(ascending=False).head(10)
        if not df.empty else pd.Series(dtype=float)
    )
    top_cited = (
        df.groupby("tgt_label")["weighted"].sum().sort_values(ascending=False).head(10)
        if not df.empty else pd.Series(dtype=float)
    )

    # Zeilen
    edges_sorted = df.sort_values("weighted", ascending=False)
    top_citers_rows = build_top_rows(top_citers)
    top_cited_rows = build_top_rows(top_cited)
    edges_rows = build_edges_rows(edges_sorted)

    # Zähler
    count_authors = int(df_nodes.shape[0]) if df_nodes is not None else 0
    count_edges = int(df.shape[0])

    # Platzhalter
    html_str = template_html
    html_str = html_str.replace("{{ASSET_REL}}", esc(compute_asset_rel(out_dir, template_dir)))
    html_str = html_str.replace("{{COUNT_AUTHORS}}", str(count_authors))
    html_str = html_str.replace("{{COUNT_EDGES}}", str(count_edges))
    html_str = html_str.replace("{{TOP_CITERS_ROWS}}", top_citers_rows)
    html_str = html_str.replace("{{TOP_CITED_ROWS}}", top_cited_rows)
    html_str = html_str.replace("{{EDGES_ROWS}}", edges_rows)

    out_path = os.path.join(out_dir, "authors_report.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_str)
    return out_path


# ---- Main --------------------------------------------------------------------
def main() -> None:
    log("Starte Render.")
    log(f"BASE_DIR = {BASE_DIR}")
    log(f"TEMPLATE_DIR = {TEMPLATE_DIR}")
    log(f"DATA_DIR = {DATA_DIR}")

    latest = find_latest_session_with_csv(DATA_DIR)
    if latest:
        session_dir = latest
        log(f"Neueste Session gefunden: {session_dir}")
    else:
        session_dir = create_session(DATA_DIR)
        log(f"Keine Session mit CSV gefunden. Neuer Ordner: {session_dir}")
        # Ohne CSVs ist kein sinnvoller Report möglich
        log("ABBRUCH: Bitte zuerst Aggregation laufen lassen, damit authors_nodes.csv und authors_edges.csv existieren.")
        sys.exit(2)

    try:
        df_nodes, df_edges = load_csvs(session_dir)
    except Exception as e:
        log(f"Fehler beim Laden der CSVs: {e}")
        sys.exit(2)

    log(f"Nodes: {df_nodes.shape[0]} | Edges: {df_edges.shape[0]}")

    try:
        tpl = read_template(TEMPLATE_HTML)
    except Exception as e:
        log(f"Fehler beim Laden des Templates: {e}")
        sys.exit(2)

    try:
        out_path = render_report_html(
            df_nodes=df_nodes,
            df_edges=df_edges,
            template_html=tpl,
            out_dir=session_dir,
            template_dir=TEMPLATE_DIR,
        )
    except Exception as e:
        log(f"Fehler beim Rendern: {e}")
        sys.exit(2)

    log(f"Fertig. Report: {out_path}")


if __name__ == "__main__":
    # Sichtbare Debug-Prints erlauben schnelle Diagnose ohne Logs.
    main()