"""
chronik_network.py

Baut ein kombiniertes Einfluss-Netzwerk aus:
1) Autoren-Kanten (Zitationen: cited → citer == tgt→src)
2) Chroniken-Kanten (pro Dokument: anderes_label → haupt_label)
3) ChronLabel→Autor-Kanten (Erwähnungen in einer Chronik: label → autor_der_chronik)

Erzeugt:
- ./output/index.html           (interaktiv: Navbar + D3-Graph)
- ./output/assets/app.js        (JS aus Template kopiert)
- ./output/assets/styles.scss    (aus SCSS kompiliert oder Fallback)
- ./output/combined_nodes.csv
- ./output/combined_edges.csv
- ./output/influence_network.png

Annahmen:
- Autoren-CSV enthält: 'src','tgt' und mindestens eine numerische Spalte aus ['weighted','total','bib_hits','text_hits'].
- Chroniken-CSV enthält: 'pdf_file','label' (optional 'context').

Dependencies:
- pandas>=2.0, networkx>=3.1, jinja2>=3.1, matplotlib>=3.7, rapidfuzz>=3.0 (optional), sass>=0.23 (optional, SCSS)
- d3 wird im HTML via CDN geladen.

Usage:
- Pfade unten konfigurieren, dann:  python chronik_network.py
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple
import os
import re
import unicodedata
import pathlib
import math
import json
import shutil

import pandas as pd
import networkx as nx
import matplotlib.pyplot as plt
from jinja2 import Environment, FileSystemLoader, select_autoescape

try:
    from rapidfuzz import fuzz, process as rf_process  # optional
    _HAS_RAPIDFUZZ = True
except Exception:
    _HAS_RAPIDFUZZ = False

try:
    import sass  # optional SCSS Compiler
    _HAS_SASS = True
except Exception:
    _HAS_SASS = False


# ---------------------------- Configuration ----------------------------

# Eingaben: passe auf Deine Pfade an oder lege die Dateien im Arbeitsverzeichnis ab.
IN_AUTHORS_CSV: str = "./authors_edges.csv"
IN_CHRONIKEN_CSV: str = "./chroniken_mentions.csv"

# Templates (Jinja + Assets). Standard: ./config/combined_network_template
TEMPLATE_DIR: str = "./config/combined_network_template"

# Ausgabe
OUTPUT_DIR: str = "./output/"
ASSETS_SUBDIR: str = "assets"

# Visualisierungs-Parameter
TOP_EDGES: int = 800
MAX_NODES: int = 600
DROP_SELF_LOOPS: bool = True

# Fuzzy-Heuristiken
ENABLE_FUZZY_JOIN: bool = True
FUZZY_THRESHOLD_NAME_JOIN: int = 92    # Name-zu-Name
FUZZY_THRESHOLD_DOC_AUTHOR: int = 88   # DocId-zu-Autor

# ---------------------------- Exceptions ----------------------------

class SchemaError(ValueError):
    """Erwartete Spalten fehlen oder unbrauchbar."""

class TemplateError(RuntimeError):
    """Template-Setup oder Rendering fehlgeschlagen."""


# ---------------------------- Utilities ----------------------------

def ensure_output_dir(path: str) -> None:
    p = pathlib.Path(path)
    p.mkdir(parents=True, exist_ok=True)


def sniff_delimiter(path: str) -> str:
    """Einfacher Delimiter-Sniffer. Fällt zurück auf ','. """
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            head = "".join([next(f) for _ in range(5)])
        semi = head.count(";")
        comma = head.count(",")
        return ";" if semi > comma else ","
    except Exception:
        return ","


_slug_rx = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    text = str(text).strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _slug_rx.sub("-", text).strip("-")
    return text


def family_from_slug(slug: str) -> str:
    return slug.split("-")[0] if slug else slug


def pick_weight_column(df: pd.DataFrame, preferred: Iterable[str]) -> str:
    for col in preferred:
        if col in df.columns:
            return col
    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    if not numeric_cols:
        raise SchemaError("Keine geeignete Gewichtsspalte gefunden.")
    return numeric_cols[0]


def head_tail(s: str, n: int = 180) -> str:
    s = str(s)
    return (s[: n] + "…") if len(s) > n else s


# ---------------------------- Loaders ----------------------------

def load_authors_edges(path: str) -> pd.DataFrame:
    if not pathlib.Path(path).exists():
        raise FileNotFoundError(f"Autoren-CSV nicht gefunden: {path}")
    sep = sniff_delimiter(path)
    df = pd.read_csv(path, sep=sep)
    expected = {"src", "tgt"}
    if not expected.issubset(df.columns):
        raise SchemaError(f"Erwartete Spalten fehlen in Autoren-CSV. Gefunden: {list(df.columns)}")
    weight_col = pick_weight_column(df, ["weighted", "total", "bib_hits", "text_hits"])
    df = df.copy()
    df["weight"] = pd.to_numeric(df[weight_col], errors="coerce").fillna(0).astype(int)
    df["src_slug"] = df["src"].map(slugify)
    df["tgt_slug"] = df["tgt"].map(slugify)
    df["src_family"] = df["src_slug"].map(family_from_slug)
    df["tgt_family"] = df["tgt_slug"].map(family_from_slug)
    ex_col = "examples" if "examples" in df.columns else None
    df["example"] = df[ex_col].astype(str).map(lambda x: head_tail(x, 220)) if ex_col else ""
    # Einflussrichtung: cited → citer  == tgt → src
    edges = (
        df.groupby(["tgt_family", "src_family"], as_index=False)
        .agg(weight=("weight", "sum"), example=("example", "first"))
        .rename(columns={"tgt_family": "u", "src_family": "v"})
    )
    edges["source_type"] = "authors"
    if DROP_SELF_LOOPS:
        edges = edges[edges["u"] != edges["v"]]
    return edges


def load_chroniken_mentions(path: str) -> pd.DataFrame:
    if not pathlib.Path(path).exists():
        raise FileNotFoundError(f"Chroniken-CSV nicht gefunden: {path}")
    sep = sniff_delimiter(path)
    df = pd.read_csv(path, sep=sep)
    expected = {"pdf_file", "label"}
    if not expected.issubset(df.columns):
        raise SchemaError(f"Erwartete Spalten fehlen in Chroniken-CSV. Gefunden: {list(df.columns)}")
    df = df.copy()
    if "context" not in df.columns:
        df["context"] = ""
    df["label_slug"] = df["label"].map(lambda s: slugify(re.sub(r"\(.*?\)", "", str(s))))
    df["label_family"] = df["label_slug"].map(family_from_slug)
    df["doc_id"] = df["pdf_file"].map(lambda p: slugify(pathlib.Path(str(p)).stem))
    return df


# ---------------------------- Name Mapping ----------------------------

def fuzzy_unify_names(names_a: List[str], names_b: List[str], threshold: int) -> Dict[str, str]:
    if not (_HAS_RAPIDFUZZ and ENABLE_FUZZY_JOIN):
        return {}
    result: Dict[str, str] = {}
    for a in names_a:
        match = rf_process.extractOne(a, names_b, scorer=fuzz.WRatio, score_cutoff=threshold)
        if match:
            b, score, _ = match
            if a != b:
                result[a] = b
    return result


def map_docs_to_authors(doc_ids: List[str], author_families: List[str]) -> Dict[str, Optional[str]]:
    mapping: Dict[str, Optional[str]] = {}
    auth_set = set(author_families)
    for d in doc_ids:
        tokens = [t for t in d.split("-") if t]
        # Direkter Token-Hit
        picked = next((t for t in tokens if t in auth_set), None)
        if not picked and _HAS_RAPIDFUZZ and ENABLE_FUZZY_JOIN:
            match = rf_process.extractOne(d, author_families, scorer=fuzz.WRatio, score_cutoff=FUZZY_THRESHOLD_DOC_AUTHOR)
            picked = match[0] if match else None
        mapping[d] = picked
    return mapping


# ---------------------------- Graph Build ----------------------------

@dataclass
class GraphData:
    nodes: pd.DataFrame
    edges: pd.DataFrame
    graph: nx.DiGraph


def build_combined_graph(df_auth_edges: pd.DataFrame, df_chron: pd.DataFrame) -> GraphData:
    # 1) Autoren-Kanten
    edges_auth = df_auth_edges[["u", "v", "weight", "example", "source_type"]].copy()

    # 2) Chroniken: pro doc Haupt-Label bestimmen; andere → Haupt-Label
    counts = df_chron.groupby(["doc_id", "label_family"]).size().reset_index(name="cnt")
    top = counts.sort_values(["doc_id", "cnt"], ascending=[True, False]).drop_duplicates(["doc_id"])
    top = top.rename(columns={"label_family": "main_family"})
    df2 = df_chron.merge(top[["doc_id", "main_family"]], on="doc_id", how="left")
    df2 = df2[df2["label_family"] != df2["main_family"]]
    edges_chron = (
        df2.groupby(["label_family", "main_family"], as_index=False)
        .agg(weight=("label_family", "size"), context=("context", "first"))
        .rename(columns={"label_family": "u", "main_family": "v"})
    )
    edges_chron["source_type"] = "chroniken"

    # 3) Label → Autor der Chronik
    author_names = sorted(set(edges_auth["u"]).union(set(edges_auth["v"])))
    doc_map = map_docs_to_authors(sorted(df_chron["doc_id"].unique()), author_names)
    df_chron["doc_author"] = df_chron["doc_id"].map(doc_map)
    edges_lab2auth = df_chron.dropna(subset=["doc_author"]).copy()
    edges_lab2auth = (
        edges_lab2auth.groupby(["label_family", "doc_author"], as_index=False)
        .size()
        .rename(columns={"label_family": "u", "doc_author": "v", "size": "weight"})
    )
    if DROP_SELF_LOOPS:
        edges_lab2auth = edges_lab2auth[edges_lab2auth["u"] != edges_lab2auth["v"]]
    edges_lab2auth["source_type"] = "chron_label_to_author"

    # 4) optional: Fuzzy-Name-Vereinigung zwischen Auth- und Chron-Namen
    names_auth = set(edges_auth["u"]).union(set(edges_auth["v"]))
    names_chron = set(edges_chron["u"]).union(set(edges_chron["v"])).union(set(edges_lab2auth["u"])).union(set(edges_lab2auth["v"]))
    map_auth2chron = fuzzy_unify_names(sorted(names_auth), sorted(names_chron), FUZZY_THRESHOLD_NAME_JOIN)
    map_chron2auth = fuzzy_unify_names(sorted(names_chron), sorted(names_auth), FUZZY_THRESHOLD_NAME_JOIN)

    def remap(df: pd.DataFrame, m1: Dict[str, str], m2: Dict[str, str]) -> pd.DataFrame:
        d = df.copy()
        d["u"] = d["u"].map(lambda x: m1.get(x, x))
        d["v"] = d["v"].map(lambda x: m1.get(x, x))
        return d

    edges_auth_m = remap(edges_auth, map_auth2chron, map_chron2auth)
    edges_chron_m = remap(edges_chron, map_chron2auth, map_auth2chron)
    edges_lab2auth_m = remap(edges_lab2auth, map_chron2auth, map_auth2chron)

    edges_all = pd.concat([edges_auth_m, edges_chron_m, edges_lab2auth_m], ignore_index=True)
    edges_all["weight"] = pd.to_numeric(edges_all["weight"], errors="coerce").fillna(0).astype(int)
    if "context" not in edges_all.columns:
        edges_all["context"] = ""
    if "example" not in edges_all.columns:
        edges_all["example"] = ""

    grouped = (
        edges_all.groupby(["u", "v", "source_type"], as_index=False)
        .agg(weight=("weight", "sum"),
             example=("example", "first"),
             context=("context", "first"))
    )
    w_total = grouped.groupby(["u", "v"], as_index=False).agg(total_weight=("weight", "sum"))
    edges_final = grouped.merge(w_total, on=["u", "v"], how="left")
    if DROP_SELF_LOOPS:
        edges_final = edges_final[edges_final["u"] != edges_final["v"]]

    # Nodes + Layer
    nodes = pd.DataFrame({"node": pd.unique(edges_final[["u", "v"]].values.ravel("K"))})
    set_auth_nodes = set(pd.unique(edges_auth_m[["u", "v"]].values.ravel("K")))
    set_label_nodes = set(pd.unique(
        pd.concat([edges_chron_m[["u", "v"]], edges_lab2auth_m[["u", "v"]]], ignore_index=True).values.ravel("K")
    ))

    def layer_for(n: str) -> str:
        a = n in set_auth_nodes
        l = n in set_label_nodes
        if a and l:
            return "both"
        if a:
            return "modern_author"
        if l:
            return "chron_label"
        return "unknown"

    nodes["layer"] = nodes["node"].map(layer_for)

    # Graph aufbauen
    G = nx.DiGraph()
    for _, row in edges_final.iterrows():
        G.add_edge(row["u"], row["v"],
                   weight=int(row["weight"]),
                   source_type=row["source_type"],
                   total_weight=int(row["total_weight"]),
                   title=_edge_title(row))
    for n in G.nodes():
        indeg = int(G.in_degree(n, weight="weight"))
        outdeg = int(G.out_degree(n, weight="weight"))
        layer = nodes.loc[nodes["node"] == n, "layer"].iloc[0]
        G.nodes[n]["layer"] = layer
        G.nodes[n]["in_w"] = indeg
        G.nodes[n]["out_w"] = outdeg
        G.nodes[n]["label"] = n.capitalize()

    # Begrenzen für Visualisierung
    G_vis = _limit_graph_for_visual(G, TOP_EDGES, MAX_NODES)

    # Exporte
    nodes_export = pd.DataFrame(
        [{"node": n,
          "layer": G.nodes[n].get("layer", ""),
          "in_weight": G.nodes[n].get("in_w", 0),
          "out_weight": G.nodes[n].get("out_w", 0)} for n in G.nodes()]
    ).sort_values(["in_weight", "out_weight"], ascending=[False, False])

    edges_export = pd.DataFrame(
        [{"u": u,
          "v": v,
          "weight": int(d.get("weight", 1)),
          "total_weight": int(d.get("total_weight", d.get("weight", 1))),
          "source_type": d.get("source_type", ""),
          "title": d.get("title", "")}
         for u, v, d in G.edges(data=True)]
    ).sort_values(["total_weight", "weight"], ascending=[False, False])

    return GraphData(nodes=nodes_export, edges=edges_export, graph=G_vis)


def _edge_title(row: pd.Series) -> str:
    parts = [f"{row['u']} → {row['v']}",
             f"src: {row['source_type']}",
             f"w={int(row['weight'])}, total={int(row['total_weight'])}"]
    if "example" in row and isinstance(row["example"], str) and row["example"]:
        parts.append(f"ex: {head_tail(row['example'], 160)}")
    if "context" in row and isinstance(row["context"], str) and row["context"]:
        parts.append(f"ctx: {head_tail(row['context'], 160)}")
    return " | ".join(parts)


def _limit_graph_for_visual(G: nx.DiGraph, top_edges: int, max_nodes: int) -> nx.DiGraph:
    def edge_score(_, __, d) -> int:
        return int(d.get("total_weight", d.get("weight", 1)))

    edges_sorted = sorted(G.edges(data=True), key=lambda e: edge_score(*e), reverse=True)
    edges_keep = edges_sorted[:top_edges]
    nodes_keep = set()
    for u, v, _ in edges_keep:
        nodes_keep.add(u)
        nodes_keep.add(v)
        if len(nodes_keep) >= max_nodes:
            break
    H = nx.DiGraph()
    for u, v, d in edges_keep:
        if u in nodes_keep and v in nodes_keep:
            H.add_edge(u, v, **d)
    for n in nodes_keep:
        if n in G.nodes:
            H.add_node(n, **G.nodes[n])
    print(f"DEBUG: Visual-Subgraph mit {H.number_of_nodes()} Knoten und {H.number_of_edges()} Kanten.")
    return H


# ---------------------------- PNG Render ----------------------------

def to_matplotlib_png(G: nx.DiGraph, out_png: str) -> None:
    plt.figure(figsize=(12, 10))
    pos = nx.spring_layout(G, k=1.5 / math.sqrt(max(1, G.number_of_nodes())), seed=42, iterations=200)
    layer_colors = {
        "modern_author": "#4C78A8",
        "chron_label": "#F58518",
        "both": "#54A24B",
        "unknown": "#999999",
    }
    node_colors = [layer_colors.get(G.nodes[n].get("layer", "unknown"), "#999999") for n in G.nodes()]
    node_sizes = [80 + math.log1p(G.nodes[n].get("in_w", 0) + G.nodes[n].get("out_w", 0)) * 30.0 for n in G.nodes()]

    nx.draw_networkx_edges(G, pos, arrows=True, arrowstyle="-|>", arrowsize=12, width=1.0, alpha=0.35)
    nx.draw_networkx_nodes(G, pos, node_color=node_colors, node_size=node_sizes, linewidths=0.5, edgecolors="#333333")
    labels = {n: G.nodes[n].get("label", n) for n in G.nodes() if (G.in_degree(n) + G.out_degree(n)) > 3}
    nx.draw_networkx_labels(G, pos, labels=labels, font_size=8)

    ensure_output_dir(os.path.dirname(out_png) or ".")
    plt.tight_layout()
    plt.savefig(out_png, dpi=160)
    plt.close()


# ---------------------------- Template Rendering ----------------------------

def render_html(template_dir: str, out_dir: str, graph_json: str, page_title: str = "Combined Influence Network") -> None:
    if not pathlib.Path(template_dir).exists():
        raise TemplateError(f"Template-Verzeichnis nicht gefunden: {template_dir}")
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "xml"]),
    )
    try:
        tpl = env.get_template("graph.html")
    except Exception as e:
        raise TemplateError(f"Template 'graph.html' nicht ladbar: {e}")

    html = tpl.render(page_title=page_title, graph_json=graph_json, nav_items=[
        {"href": "#", "label": "Netzwerk"},
        {"href": "#about", "label": "Über"},
    ])

    ensure_output_dir(out_dir)
    index_path = os.path.join(out_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"DEBUG: HTML gerendert → {index_path}")

    # Assets kopieren/kompilieren
    assets_out = os.path.join(out_dir, ASSETS_SUBDIR)
    ensure_output_dir(assets_out)

    # app.js
    js_src = os.path.join(template_dir, "app.js")
    if not pathlib.Path(js_src).exists():
        raise TemplateError("app.js fehlt im Template-Verzeichnis.")
    shutil.copyfile(js_src, os.path.join(assets_out, "app.js"))
    print(f"DEBUG: JS kopiert → {os.path.join(assets_out, 'app.js')}")

    # CSS: SCSS kompilieren, sonst Fallback styles.scss
    scss_src = os.path.join(template_dir, "styles.scss")
    css_out = os.path.join(assets_out, "styles.scss")
    css_fallback_src = os.path.join(template_dir, "styles.scss")
    if _HAS_SASS and pathlib.Path(scss_src).exists():
        try:
            css = sass.compile(filename=scss_src, output_style="compressed")
            with open(css_out, "w", encoding="utf-8") as f:
                f.write(css)
            print(f"DEBUG: SCSS kompiliert → {css_out}")
        except Exception as e:
            print(f"WARN: SCSS-Kompilierung fehlgeschlagen: {e}")
            if pathlib.Path(css_fallback_src).exists():
                shutil.copyfile(css_fallback_src, css_out)
                print(f"DEBUG: CSS Fallback kopiert → {css_out}")
            else:
                raise TemplateError("Weder SCSS kompiliert noch CSS-Fallback vorhanden.")
    else:
        if pathlib.Path(css_fallback_src).exists():
            shutil.copyfile(css_fallback_src, css_out)
            print(f"DEBUG: CSS Fallback kopiert → {css_out}")
        else:
            raise TemplateError("SCSS nicht verfügbar und CSS-Fallback fehlt.")


# ---------------------------- Main ----------------------------

def main() -> None:
    print("DEBUG: Starte Pipeline.")
    print(f"DEBUG: Autoren-CSV:   {os.path.abspath(IN_AUTHORS_CSV)}")
    print(f"DEBUG: Chroniken-CSV: {os.path.abspath(IN_CHRONIKEN_CSV)}")
    print(f"DEBUG: Template-Dir:  {os.path.abspath(TEMPLATE_DIR)}")

    # Laden
    df_auth_edges = load_authors_edges(IN_AUTHORS_CSV)
    print(f"DEBUG: Autoren-Kanten: {len(df_auth_edges)}")

    df_chron = load_chroniken_mentions(IN_CHRONIKEN_CSV)
    print(f"DEBUG: Chroniken-Zeilen: {len(df_chron)}")

    # Kombinieren
    gd = build_combined_graph(df_auth_edges, df_chron)

    # Export CSVs
    ensure_output_dir(OUTPUT_DIR)
    nodes_csv = os.path.join(OUTPUT_DIR, "combined_nodes.csv")
    edges_csv = os.path.join(OUTPUT_DIR, "combined_edges.csv")
    gd.nodes.to_csv(nodes_csv, index=False)
    gd.edges.to_csv(edges_csv, index=False)
    print(f"DEBUG: Nodes exportiert → {nodes_csv}  ({len(gd.nodes)} Zeilen)")
    print(f"DEBUG: Edges exportiert → {edges_csv}  ({len(gd.edges)} Zeilen)")

    # PNG
    png_path = os.path.join(OUTPUT_DIR, "influence_network.png")
    to_matplotlib_png(gd.graph, png_path)
    print(f"DEBUG: Statisches PNG → {png_path}")

    # JSON für HTML
    nodes_list = [
        {"id": n,
         "label": gd.graph.nodes[n].get("label", n),
         "layer": gd.graph.nodes[n].get("layer", ""),
         "in_w": int(gd.graph.nodes[n].get("in_w", 0)),
         "out_w": int(gd.graph.nodes[n].get("out_w", 0))}
        for n in gd.graph.nodes()
    ]
    links_list = [
        {"source": u,
         "target": v,
         "weight": int(d.get("weight", 1)),
         "total_weight": int(d.get("total_weight", d.get("weight", 1))),
         "source_type": d.get("source_type", ""),
         "title": d.get("title", "")}
        for u, v, d in gd.graph.edges(data=True)
    ]
    graph_json = json.dumps({"nodes": nodes_list, "links": links_list}, ensure_ascii=False)

    # Render HTML
    render_html(template_dir=TEMPLATE_DIR, out_dir=OUTPUT_DIR, graph_json=graph_json)

    print("DEBUG: Top 10 Knoten nach In-Gewicht:")
    print(gd.nodes.head(10).to_string(index=False))
    print("DEBUG: Fertig.")


if __name__ == "__main__":
    main()