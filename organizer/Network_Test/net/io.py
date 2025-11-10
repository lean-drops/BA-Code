from __future__ import annotations
import os
import pandas as pd
import networkx as nx
from .utils import debug

def load_mentions_csv(path: str) -> pd.DataFrame:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"CSV nicht gefunden: {path}")
    try:
        df = pd.read_csv(path, sep=';', engine='python')
    except Exception as e:
        debug(f"[io] sep=';' scheiterte: {e}; sniffe Separator…")
        df = pd.read_csv(path, sep=None, engine='python')
    if df.empty:
        raise ValueError("CSV ist leer.")
    debug(f"[io] CSV geladen: rows={len(df)} cols={list(df.columns)}")
    return df

def resolve_columns(df: pd.DataFrame) -> tuple[str, str]:
    doc_candidates = ["pdf_file", "pdf", "document", "source", "file", "filename", "doc"]
    work_candidates = ["label", "canonical", "work", "title", "chronik", "edition", "name", "match", "normalized"]
    lower = {c.lower(): c for c in df.columns}
    doc_col = next((lower[c] for c in doc_candidates if c in lower), None)
    work_col = next((lower[c] for c in work_candidates if c in lower), None)
    if not doc_col:
        raise KeyError(f"Dokumentspalte nicht erkannt. Erwartet eine aus {doc_candidates}. Vorhanden: {list(df.columns)}")
    if not work_col:
        raise KeyError(f"Werkspalte nicht erkannt. Erwartet eine aus {work_candidates}. Vorhanden: {list(df.columns)}")
    debug(f"[io] Spalten: doc={doc_col} work={work_col}")
    return doc_col, work_col

def _id_pdf(path: str) -> str: return f"P|{os.path.basename(path)}"
def _id_chronik(name: str) -> str: return f"C|{name}"

def build_bipartite_graph(df: pd.DataFrame, doc_col: str, work_col: str) -> nx.Graph:
    df = df[[doc_col, work_col]].dropna()
    df[doc_col] = df[doc_col].astype(str).str.strip()
    df[work_col] = df[work_col].astype(str).str.strip()
    pairs = df.groupby([doc_col, work_col]).size().reset_index(name="w")
    chronik_docs = pairs.groupby(work_col)[doc_col].nunique().to_dict()
    pdf_chroniks = pairs.groupby(doc_col)[work_col].nunique().to_dict()
    G = nx.Graph(name="chroniken_library↔Werke")
    for ch, n_docs in chronik_docs.items():
        G.add_node(_id_chronik(ch), label=ch, role="chronik", bipartite=0, mentions=int(n_docs))
    for pdf, n_ch in pdf_chroniks.items():
        G.add_node(_id_pdf(pdf), label=os.path.basename(pdf) or pdf, role="work", bipartite=1, items=int(n_ch))
    for _, row in pairs.iterrows():
        G.add_edge(_id_pdf(row[doc_col]), _id_chronik(row[work_col]), weight=float(row["w"]))
    debug(f"[graph] gebaut: nodes={G.number_of_nodes()} edges={G.number_of_edges()}")
    return G

def node_mass(G: nx.Graph, n: str) -> float:
    d = G.nodes[n]
    return 1.0 + (d.get("mentions") or d.get("items") or 1) ** 0.5

def weighted_degree(G: nx.Graph, n: str) -> float:
    return sum(float(d.get("weight", 1.0)) for _u, _v, d in G.edges(n, data=True))

