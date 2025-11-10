#!/usr/bin/env python3
"""
Flask-Webapp für chroniken_library ↔ Werke.
Templates und Assets liegen in config/network_template.
Start: python app.py
"""
from __future__ import annotations

import os
import math
import traceback
from dataclasses import dataclass
from typing import Dict, List, Tuple

import pandas as pd
import networkx as nx
from flask import Flask, jsonify, render_template, request

# ------------------------ Debug ------------------------

def debug(msg: str) -> None:
    print(f"[DEBUG] {msg}", flush=True)

# ------------------------ CSV / Graph ------------------------

def load_mentions_csv(path: str) -> pd.DataFrame:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"CSV nicht gefunden: {path}")
    try:
        df = pd.read_csv(path, sep=";", engine="python")
    except Exception as e1:
        debug(f"Lesen mit sep=';' scheiterte → {e1}. Versuche Auto-Sniff.")
        df = pd.read_csv(path, sep=None, engine="python")
    if df.empty:
        raise ValueError("CSV ist leer.")
    return df

def resolve_columns(df: pd.DataFrame) -> Tuple[str, str]:
    doc_candidates = ["pdf_file", "pdf", "document", "source", "file", "filename", "doc"]
    work_candidates = ["label", "canonical", "work", "title", "chronik", "edition", "name", "match", "normalized"]
    lower = {c.lower(): c for c in df.columns}
    doc_col = next((lower[c] for c in doc_candidates if c in lower), None)
    work_col = next((lower[c] for c in work_candidates if c in lower), None)
    if not doc_col:
        raise KeyError(f"Dokumentspalte nicht erkannt. Erwartet: {doc_candidates}. Vorhanden: {list(df.columns)}")
    if not work_col:
        raise KeyError(f"Werkspalte nicht erkannt. Erwartet: {work_candidates}. Vorhanden: {list(df.columns)}")
    return doc_col, work_col

def _id_pdf(path: str) -> str:
    return f"P|{os.path.basename(path)}"

def _id_chronik(name: str) -> str:
    return f"C|{name}"

def _node_mass(role: str, mentions: int = 1, items: int = 1) -> float:
    return 1.0 + math.sqrt(max(1, mentions if role == "chronik" else items))

@dataclass
class BuiltGraph:
    nodes: List[dict]
    edges: List[dict]

def build_bipartite_graph(df: pd.DataFrame) -> BuiltGraph:
    doc_col, work_col = resolve_columns(df)
    tmp = df[[doc_col, work_col]].dropna().astype(str)
    tmp[doc_col] = tmp[doc_col].str.strip()
    tmp[work_col] = tmp[work_col].str.strip()

    pairs = tmp.groupby([doc_col, work_col]).size().reset_index(name="w")
    chronik_docs = pairs.groupby(work_col)[doc_col].nunique().to_dict()
    pdf_chroniks = pairs.groupby(doc_col)[work_col].nunique().to_dict()

    G = nx.Graph(name="chroniken_library↔Werke")
    for ch, n_docs in chronik_docs.items():
        nid = _id_chronik(ch)
        G.add_node(nid, label=ch, role="chronik", mentions=int(n_docs))
    for pdf, n_ch in pdf_chroniks.items():
        nid = _id_pdf(pdf)
        G.add_node(nid, label=os.path.basename(pdf) or pdf, role="work", items=int(n_ch))
    for _, row in pairs.iterrows():
        u = _id_pdf(row[doc_col]); v = _id_chronik(row[work_col])
        if u in G and v in G:
            G.add_edge(u, v, weight=float(row["w"]))

    # stabile, breite bipartite Startposition (preset)
    pos = _bipartite_ordered_layout(G, scale=320.0, row_step=46.0)

    nodes, edges = [], []
    for n, d in G.nodes(data=True):
        role = d.get("role", "")
        mass = _node_mass(role, int(d.get("mentions", 1)), int(d.get("items", 1)))
        nodes.append({
            "data": {
                "id": n, "label": d.get("label", str(n)), "role": role,
                "mentions": int(d.get("mentions", 0)), "items": int(d.get("items", 0)), "mass": float(mass)
            },
            "position": {"x": float(pos.get(n, (0.0, 0.0))[0]), "y": float(pos.get(n, (0.0, 0.0))[1])}
        })
    for u, v, ed in G.edges(data=True):
        edges.append({"data": {"id": f"{u}__{v}", "source": u, "target": v, "weight": float(ed.get("weight", 1.0))}})
    debug(f"Graph gebaut: nodes={len(nodes)} edges={len(edges)}")
    return BuiltGraph(nodes=nodes, edges=edges)

def _bipartite_ordered_layout(G: nx.Graph, scale: float = 320.0, row_step: float = 46.0) -> Dict[str, Tuple[float, float]]:
    left = [n for n, d in G.nodes(data=True) if d.get("role") == "work"]
    right = [n for n, d in G.nodes(data=True) if d.get("role") == "chronik"]

    def _mass(n: str) -> float:
        d = G.nodes[n]
        return _node_mass(d.get("role", ""), int(d.get("mentions", 1)), int(d.get("items", 1)))

    def _order(side: List[str], other: List[str]) -> List[str]:
        idx = {n: i for i, n in enumerate(other)}
        score = {}
        for n in side:
            neigh = list(G.neighbors(n))
            if not neigh:
                score[n] = (0.0, 1e-9); continue
            s = sum(idx.get(m, 0) * _mass(m) for m in neigh)
            w = sum(_mass(m) for m in neigh)
            score[n] = (s, w)
        return sorted(side, key=lambda nn: score[nn][0] / score[nn][1])

    for _ in range(2):
        left = _order(left, right)
        right = _order(right, left)

    pos: Dict[str, Tuple[float, float]] = {}
    def _coords(lst: List[str], xval: float) -> None:
        n = max(1, len(lst))
        for i, node in enumerate(lst):
            y = (i - (n - 1) / 2.0) * row_step
            pos[node] = (xval, y)
    _coords(left, -scale)
    _coords(right, scale)
    return pos

# ------------------------ Flask ------------------------

TEMPLATE_DIR = "config/network_template"
app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=TEMPLATE_DIR, static_url_path="/assets")
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64 MB

@app.get("/")
def index():
    return render_template("index.html")

def _graph_payload_from_df(df: pd.DataFrame) -> Dict[str, object]:
    built = build_bipartite_graph(df)
    return {"nodes": built.nodes, "edges": built.edges, "layoutName": "preset", "showLabels": True, "minWeight": 1}

@app.post("/api/upload_csv")
def upload_csv():
    try:
        if "file" not in request.files:
            return jsonify({"error": "Kein 'file' im Form-Data."}), 400
        file = request.files["file"]
        if not file.filename:
            return jsonify({"error": "Leerer Dateiname."}), 400
        try:
            df = pd.read_csv(file, sep=";", engine="python")
        except Exception:
            file.stream.seek(0)
            df = pd.read_csv(file, sep=None, engine="python")
        if df.empty:
            return jsonify({"error": "CSV ist leer."}), 400
        payload = _graph_payload_from_df(df)
        payload["filename"] = file.filename
        debug(f"Upload verarbeitet: {file.filename} rows={len(df)}")
        return jsonify(payload)
    except Exception as ex:
        traceback.print_exc()
        return jsonify({"error": f"Upload/Parse-Fehler: {ex}"}), 500

@app.get("/api/autoload")
def autoload():
    try:
        cands = [
            os.environ.get("CHRONIKEN_MENTIONS_CSV", "").strip(),
            os.path.join(os.getcwd(), "chroniken_mentions.csv"),
            "/Users/programming/PycharmProjects/Find_Bibliography_NEw/data/azk_library/chroniken_mentions.csv",
        ]
        for c in cands:
            if c and os.path.isfile(c):
                df = load_mentions_csv(c)
                payload = _graph_payload_from_df(df)
                payload["filename"] = os.path.basename(c)
                debug(f"Auto-Load: {c}")
                return jsonify(payload)
        return jsonify({"error": "Keine Auto-Load CSV gefunden."}), 404
    except Exception as ex:
        traceback.print_exc()
        return jsonify({"error": f"Auto-Load Fehler: {ex}"}), 500

@app.get("/api/health")
def health():
    return jsonify({"ok": True})

def main() -> None:
    debug("Starte Flask Web-App …")
    debug(f"TEMPLATE_DIR: {os.path.abspath(TEMPLATE_DIR)}")
    host, port = "127.0.0.1", 5000
    debug(f"Öffne im Browser: http://{host}:{port}")
    app.run(host=host, port=port, debug=False)

if __name__ == "__main__":
    main()

