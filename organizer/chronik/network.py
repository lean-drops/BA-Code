#!/usr/bin/env python3
"""
chroniken_navigator_cyto.py — Interaktives Netz (chroniken_library ↔ Werke) mit Cytoscape.js in QtWebEngine.

Warum diese Version?
- Rendering: Cytoscape.js ist für große Graphen optimiert (GPU, Styles, Klassen), stabiler als manuelles QPainter-Zeichnen.
- Features: bipartites/preset-Layout, fcose, breadthfirst; Suche + Tab-Zyklus; Richtungsnavigation per Pfeilen;
  invertierte Gegenrollen-Hervorhebung; Labels- und Kantenschwelle-Toggles; PNG-Export.
- Ein-Datei-App. Keine externen HTML/JS-Dateien.

Abhängigkeiten:
    pip install PyQt5 PyQtWebEngine pandas networkx

Nutzung:
    $ python chroniken_navigator_cyto.py
    • CSV laden: Button „CSV laden…“ oder QSettings-Pfad.
    • Suche: Text eintippen → „Hervorheben“; Tab / Shift+Tab zyklisch.
    • Navigation: ← → ↑ ↓ Richtungs-Sprung; F Fit; +/− Zoom; Esc Fokus löschen.
    • Toggle „Nicht-verbundene hervorheben (Gegenrolle)“ wie beschrieben.
    • Kantenschwelle-Filter und Label-Toggle live.
    • Export PNG.

Annahmen:
- CSV hat Spalten ähnlich: pdf_file|pdf|document|source|file|filename|doc  und  label|canonical|work|title|chronik|edition|name|match|normalized.
- Kanten-Gewicht = Anzahl Erwähnungen je (PDF, Chronik).

© synthetic. Debug-Ausgaben über print().
"""

from __future__ import annotations

import json
import math
import os
import sys
import traceback
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd
import networkx as nx

# Qt
from PyQt5 import QtCore, QtGui, QtWidgets
from PySide6 import QtWebEngineWidgets


# ------------------------ Debug ------------------------

def debug(msg: str) -> None:
    print(f"[DEBUG] {msg}", flush=True)


# ------------------------ CSV / Graphbau ------------------------

def load_mentions_csv(path: str) -> pd.DataFrame:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"CSV nicht gefunden: {path}")
    try:
        df = pd.read_csv(path, sep=';', engine='python')
    except Exception as e1:
        debug(f"CSV lesen mit sep=';' scheiterte → {e1}. Versuche Auto-Sniff.")
        df = pd.read_csv(path, sep=None, engine='python')
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
    G: nx.Graph
    positions: Dict[str, Tuple[float, float]]  # preset/bipartit
    nodes: List[dict]  # cytoscape format
    edges: List[dict]  # cytoscape format


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
        u = _id_pdf(row[doc_col])
        v = _id_chronik(row[work_col])
        if u in G and v in G:
            G.add_edge(u, v, weight=float(row["w"]))

    pos = _bipartite_ordered_layout(G, scale=220.0)  # Pixel

    nodes, edges = [], []
    for n, d in G.nodes(data=True):
        role = d.get("role")
        mass = _node_mass(role, d.get("mentions", 1), d.get("items", 1))
        nodes.append({
            "data": {
                "id": n,
                "label": d.get("label", str(n)),
                "role": role,
                "mentions": int(d.get("mentions", 0)),
                "items": int(d.get("items", 0)),
                "mass": float(mass),
            },
            "position": {"x": float(pos.get(n, (0.0, 0.0))[0]), "y": float(pos.get(n, (0.0, 0.0))[1])}
        })
    for u, v, ed in G.edges(data=True):
        w = float(ed.get("weight", 1.0))
        edges.append({
            "data": {
                "id": f"{u}__{v}",
                "source": u,
                "target": v,
                "weight": w
            }
        })
    debug(f"Graph gebaut: nodes={len(nodes)} edges={len(edges)}")
    return BuiltGraph(G=G, positions=pos, nodes=nodes, edges=edges)


def _bipartite_ordered_layout(G: nx.Graph, scale: float = 200.0) -> Dict[str, Tuple[float, float]]:
    left = [n for n, d in G.nodes(data=True) if d.get("role") == "work"]
    right = [n for n, d in G.nodes(data=True) if d.get("role") == "chronik"]

    def _mass(n: str) -> float:
        d = G.nodes[n]
        return _node_mass(d.get("role"), d.get("mentions", 1), d.get("items", 1))

    def _order(side: List[str], other: List[str]) -> List[str]:
        idx = {n: i for i, n in enumerate(other)}
        wsum = {}
        for n in side:
            neigh = list(G.neighbors(n))
            if not neigh:
                wsum[n] = (0.0, 1e-9)
            else:
                s = 0.0
                w = 0.0
                for m in neigh:
                    s += idx.get(m, 0) * _mass(m)
                    w += _mass(m)
                wsum[n] = (s, w)
        return sorted(side, key=lambda nn: wsum[nn][0] / wsum[nn][1])

    for _ in range(2):
        left = _order(left, right)
        right = _order(right, left)

    pos: Dict[str, Tuple[float, float]] = {}

    def _coords(lst: List[str], xval: float) -> None:
        n = max(1, len(lst))
        for i, node in enumerate(lst):
            y = (i - (n - 1) / 2.0) * 28.0  # Zeilenabstand
            pos[node] = (xval, y)

    _coords(left, -scale)
    _coords(right, scale)
    return pos


# ------------------------ HTML / JS (Cytoscape in WebEngine) ------------------------

HTML_PAGE = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Chroniken↔Werke</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'self' https://unpkg.com; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self' https://unpkg.com 'unsafe-inline' 'unsafe-eval';">
<style>
  html, body, #cy { height:100%; width:100%; margin:0; padding:0; background:#0b0f14; }
</style>
<script src="https://unpkg.com/cytoscape@3.28.0/dist/cytoscape.min.js"></script>
<script src="https://unpkg.com/cytoscape-fcose@2.2.1/cytoscape-fcose.js"></script>
</head>
<body>
<div id="cy"></div>
<script>
(function(){
  let cy = null;
  let invertFocus = false;
  let showLabels = true;
  let minWeight = 1;
  let sticky = false;
  let currentId = null;
  let searchHits = [];
  let searchIndex = -1;

  function baseStyle() {
    return [
      { selector: 'core', style: { 'selection-box-color': '#3b82f6', 'selection-box-opacity': 0.15 } },
      { selector: 'node', style: {
          'shape': 'ellipse',
          'width': 'mapData(mass, 1, 15, 20, 62)',
          'height': 'mapData(mass, 1, 15, 20, 62)',
          'background-color': ele => ele.data('role')==='chronik' ? '#1d4f58' : '#c89b3c',
          'border-color': ele => ele.data('role')==='chronik' ? '#0a1a1f' : '#5a3f0b',
          'border-width': 1,
          'label': 'data(label)',
          'font-size': 12,
          'color': '#f4f1e9',
          'text-background-color': '#0b0f14',
          'text-background-opacity': 0.75,
          'text-background-padding': 2,
          'text-halo-color': '#0b0f14',
          'text-halo-opacity': 0.9,
          'text-halo-blur': 1,
          'text-opacity': 1,
          'z-index-compare': 'manual',
          'z-index': 0
      }},
      { selector: 'node[role = "work"]', style: { 'shape': 'diamond' } },
      { selector: 'edge', style: {
          'line-color': '#6b7280',
          'width': 'mapData(weight, 1, 15, 1, 4)',
          'curve-style': 'bezier',
          'opacity': 0.95
      }},
      { selector: '.dim', style: { 'opacity': 0.60 } },
      { selector: '.emph', style: { 'border-width': 3, 'border-color': '#ef4444', 'z-index': 1000, 'opacity': 1, 'text-opacity': 1 } },
      { selector: '.edge-highlight', style: { 'line-color': '#ef4444', 'width': 3 } },
      { selector: '.hit', style: { 'border-width': 3, 'border-color': '#3b82f6', 'z-index': 900, 'text-opacity': 1 } },
      { selector: '.hide', style: { 'display': 'none' } }
    ];
  }

  function clearFocus() {
    if (!cy) return;
    cy.nodes().removeClass('emph dim');
    cy.edges().removeClass('edge-highlight');
    currentId = null;
    sticky = false;
  }

  function applyFocus(node, makeSticky) {
    if (!cy || !node || node.empty()) return;
    cy.nodes().removeClass('emph dim');
    cy.edges().removeClass('edge-highlight');
    const role = node.data('role');
    node.addClass('emph');
    if (invertFocus) {
      const oppRole = role === 'chronik' ? 'work' : 'chronik';
      const neighbors = node.neighborhood('node');
      const neighborSet = new Set(neighbors.filter(`[role = "${oppRole}"]`).map(n => n.id()));
      const oppNodes = cy.nodes(`[role = "${oppRole}"]`);
      const nonNeighbors = oppNodes.filter(n => !neighborSet.has(n.id()));
      nonNeighbors.addClass('emph');
      cy.nodes().not(node).not(nonNeighbors).addClass('dim'); // nur abgedunkelt
      // Kanten bleiben unverändert
    } else {
      const neighNodes = node.closedNeighborhood('node');
      const neighEdges = node.connectedEdges();
      neighNodes.addClass('emph');
      neighEdges.addClass('edge-highlight');
      cy.nodes().difference(neighNodes).addClass('dim');
      // Kanten außerhalb bleiben Standard
    }
    currentId = node.id();
    sticky = !!makeSticky;
  }

  function updateEdgeThreshold() {
    if (!cy) return;
    cy.edges().forEach(e => {
      const w = e.data('weight') || 1;
      if (w < minWeight) e.addClass('hide'); else e.removeClass('hide');
    });
  }

  function toggleLabels(show) {
    showLabels = !!show;
    if (!cy) return;
    const val = showLabels ? 1 : 0;
    cy.style().selector('node').style('text-opacity', val).update();
    // Emphasis/Hit heben Text ohnehin hervor
  }

  function allCandidates() {
    if (!cy) return cy.collection();
    if (searchHits.length > 0) {
      const ids = searchHits.join(',');
      return cy.nodes(ids);
    }
    return cy.nodes();
  }

  function jumpDirectional(dx, dy) {
    if (!cy) return;
    const vlen = Math.hypot(dx, dy) || 1;
    const ux = dx / vlen, uy = dy / vlen;

    const origin = (currentId && cy.$id(currentId).nonempty()) ?
      cy.$id(currentId).position() :
      { x: cy.extent().x1 + (cy.extent().w/2), y: cy.extent().y1 + (cy.extent().h/2) };

    let bestNode = null;
    let bestKey = [-2.0, Infinity]; // proj, 1/dist

    allCandidates().forEach(n => {
      if (!n.nonempty()) return;
      if (currentId && n.id() === currentId) return;
      const p = n.position();
      const dxn = p.x - origin.x, dyn = p.y - origin.y;
      const dist = Math.hypot(dxn, dyn) || 1e-6;
      const proj = (dxn * ux + dyn * uy) / dist; // Richtungskosinus
      let key = [proj, 1.0/dist];
      // Primär proj, sekundär Nähe
      if (key[0] > bestKey[0] + 1e-6 || (Math.abs(key[0]-bestKey[0])<1e-6 and key[1]>bestKey[1])):
          pass
      if (key[0] > bestKey[0] or (Math.abs(key[0]-bestKey[0])<1e-6 and key[1]>bestKey[1])) {
        bestNode = n; bestKey = key;
      }
    });
    if (!bestNode) {
      // Fallback: bestes proj egal Vorzeichen
      bestKey = [-2.0, Infinity];
      allCandidates().forEach(n => {
        if (!n.nonempty()) return;
        if (currentId && n.id() === currentId) return;
        const p = n.position();
        const dxn = p.x - origin.x, dyn = p.y - origin.y;
        const dist = Math.hypot(dxn, dyn) || 1e-6;
        const proj = (dxn * ux + dyn * uy) / dist;
        const key = [proj, 1.0/dist];
        if (key[0] > bestKey[0] or (Math.abs(key[0]-bestKey[0])<1e-6 and key[1]>bestKey[1])) {
          bestNode = n; bestKey = key;
        }
      });
    }
    if (bestNode) {
      applyFocus(bestNode, true);
      cy.animate({center: {eles: bestNode}, duration: 120});
    }
  }

  function bindEvents() {
    if (!cy) return;
    cy.on('tap', 'node', (ev) => applyFocus(ev.target, true));
    cy.on('tap', (ev) => { if (ev.target === cy) { clearFocus(); }});
    cy.on('mouseover', 'node', (ev) => { if (!sticky) applyFocus(ev.target, false); });
    cy.on('mouseout', 'node', () => { if (!sticky) clearFocus(); });

    document.addEventListener('keydown', (e) => {
      const k = e.key;
      if (k === 'Escape') { clearFocus(); }
      else if (k === 'f' || k === 'F') { cy.fit(); }
      else if (k === '+' || k === '=') { cy.zoom(cy.zoom()*1.15); }
      else if (k === '-') { cy.zoom(cy.zoom()/1.15); }
      else if (k === 'ArrowLeft') { e.preventDefault(); jumpDirectional(-1,0); }
      else if (k === 'ArrowRight') { e.preventDefault(); jumpDirectional(1,0); }
      else if (k === 'ArrowUp') { e.preventDefault(); jumpDirectional(0,-1); }
      else if (k === 'ArrowDown') { e.preventDefault(); jumpDirectional(0,1); }
      else if (k === 'Tab') {
        e.preventDefault();
        if (searchHits.length > 0) {
          if (!e.shiftKey) searchIndex = (searchIndex + 1) % searchHits.length;
          else searchIndex = (searchIndex - 1 + searchHits.length) % searchHits.length;
          const n = cy.$id(searchHits[searchIndex]);
          if (n.nonempty()) { applyFocus(n, true); cy.animate({center:{eles:n}, duration:120}); }
        }
      }
    });
  }

  // ---------------- API für Python ----------------

  window.setGraph = function(payload) {
    // payload: {nodes:[], edges:[], layoutName: 'preset'|'fcose'|'breadthfirst', showLabels: bool, minWeight:int}
    const elements = (payload && payload.nodes) ? payload.nodes.concat(payload.edges) : [];
    if (cy) { cy.destroy(); cy = null; }
    cy = cytoscape({
      container: document.getElementById('cy'),
      elements: elements,
      style: baseStyle(),
      layout: { name: 'preset' },
      wheelSensitivity: 0.2,
      pixelRatio: 1
    });
    showLabels = !!(payload && payload.showLabels);
    minWeight = (payload && payload.minWeight) ? payload.minWeight : 1;
    toggleLabels(showLabels);
    updateEdgeThreshold();
    bindEvents();
    if (payload && payload.layoutName && payload.layoutName !== 'preset') {
      window.setLayout(payload.layoutName);
    } else {
      cy.fit();
    }
    return true;
  };

  window.setLayout = function(name) {
    if (!cy) return false;
    const lname = name || 'preset';
    let opts = { name: 'preset' };
    if (lname === 'fcose') {
      opts = { name: 'fcose', animate: false, quality: 'default', randomize: false, nodeSeparation: 50, idealEdgeLength: 80 };
    } else if (lname === 'breadthfirst') {
      // Versetzte Ebenen nach Rolle
      opts = { name: 'breadthfirst', directed: false, spacingFactor: 1.2, wrap: true, roots: cy.nodes('[role="work"]') };
    } else if (lname === 'grid') {
      opts = { name: 'grid', avoidOverlap: true, condense: true };
    } else {
      opts = { name: 'preset' };
    }
    cy.layout(opts).run();
    cy.fit();
    return true;
  };

  window.setInvertFocus = function(on) { invertFocus = !!on; if (cy) clearFocus(); return invertFocus; };
  window.toggleLabels = function(show) { toggleLabels(show); return showLabels; };
  window.setThreshold = function(minW) { minWeight = parseInt(minW||1); updateEdgeThreshold(); return minWeight; };

  window.applySearch = function(term) {
    if (!cy) return 0;
    const t = (term||'').trim().toLowerCase();
    cy.nodes().removeClass('hit');
    searchHits = []; searchIndex = -1;
    if (t.length === 0) return 0;
    cy.nodes().forEach(n => {
      const label = (n.data('label')||'').toLowerCase();
      const hit = label.includes(t);
      if (hit) { n.addClass('hit'); searchHits.push(n.id()); }
    });
    // Sortiere Hits grob top→bottom, dann links→rechts
    searchHits.sort((a,b) => {
      const pa = cy.$id(a).position(), pb = cy.$id(b).position();
      if (Math.round(pa.y) !== Math.round(pb.y)) return Math.round(pa.y) - Math.round(pb.y);
      return Math.round(pa.x) - Math.round(pb.x);
    });
    return searchHits.length;
  };

  window.exportPng = function() {
    if (!cy) return null;
    const dataUrl = cy.png({ full: true, scale: 2, bg: '#0b0f14' });
    return dataUrl; // Python decodiert und speichert
  };

})();
</script>
</body>
</html>
"""


# ------------------------ Qt-UI ------------------------

class WebView(QtWebEngineWidgets.QWebEngineView):
    """QWebEngineView mit Utility run_js()."""
    def __init__(self) -> None:
        super().__init__()
        self.setContextMenuPolicy(QtCore.Qt.NoContextMenu)
        self.setZoomFactor(1.0)

    def run_js(self, code: str, callback=None) -> None:
        self.page().runJavaScript(code, callback)


class MainWindow(QtWidgets.QMainWindow):
    SETTINGS_KEY_LAST_CSV = "last_csv_path"

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("chroniken_library↔Werke — Interaktives Netz (Cytoscape.js)")
        self.resize(1280, 820)

        self.csv_path: Optional[str] = None
        self.df: Optional[pd.DataFrame] = None
        self.current_layout: str = "preset"  # preset, fcose, breadthfirst, grid
        self.min_w: int = 1
        self.show_labels: bool = True

        self.view = WebView()
        self.view.setHtml(HTML_PAGE, QtCore.QUrl("https://local.app/"))
        self._build_controls()
        self._load_last_csv_if_available()

    # ---------- UI Aufbau ----------

    def _build_controls(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        # Controls
        self.btn_load_csv = QtWidgets.QPushButton("CSV laden…")
        self.btn_load_csv.clicked.connect(self.on_load_csv)

        self.cmb_layout = QtWidgets.QComboBox()
        self.cmb_layout.addItems(["preset", "fcose", "breadthfirst", "grid"])
        self.cmb_layout.currentTextChanged.connect(self.on_layout_change)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setMinimum(1)
        self.slider.setMaximum(10)
        self.slider.setValue(self.min_w)
        self.slider.valueChanged.connect(self.on_slider)
        self.lbl_thresh = QtWidgets.QLabel(f"Kantenschwelle: ≥ {self.min_w}")

        self.chk_labels = QtWidgets.QCheckBox("Labels")
        self.chk_labels.setChecked(self.show_labels)
        self.chk_labels.stateChanged.connect(self.on_labels_toggle)

        self.chk_invert_focus = QtWidgets.QCheckBox("Nicht-verbundene hervorheben (Gegenrolle)")
        self.chk_invert_focus.setChecked(False)
        self.chk_invert_focus.stateChanged.connect(self.on_invert_focus_toggle)

        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Knoten suchen…")
        self.btn_search = QtWidgets.QPushButton("Hervorheben")
        self.btn_search.clicked.connect(self.on_search)

        self.btn_export = QtWidgets.QPushButton("PNG exportieren…")
        self.btn_export.clicked.connect(self.on_export_png)

        # Layout links
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(10)
        left.addWidget(self.btn_load_csv)
        row1 = QtWidgets.QHBoxLayout()
        row1.addWidget(QtWidgets.QLabel("Layout:"))
        row1.addWidget(self.cmb_layout, 1)
        left.addLayout(row1)
        left.addWidget(self.lbl_thresh)
        left.addWidget(self.slider)
        left.addWidget(self.chk_labels)
        left.addWidget(self.chk_invert_focus)
        left.addSpacing(8)
        left.addWidget(QtWidgets.QLabel("Suche:"))
        left.addWidget(self.search_edit)
        left.addWidget(self.btn_search)
        left.addSpacing(8)
        left.addWidget(self.btn_export)
        left.addStretch(1)

        left_box = QtWidgets.QFrame()
        left_box.setLayout(left)
        left_box.setFixedWidth(300)
        left_box.setStyleSheet("""
            QFrame { background:#0f172a; }
            QLabel, QCheckBox { color:#e2e8f0; }
            QPushButton { background:#1e293b; color:#e2e8f0; border:1px solid #334155; padding:6px; border-radius:6px; }
            QPushButton:hover { background:#273449; }
            QComboBox, QLineEdit { background:#0b1320; color:#e2e8f0; border:1px solid #334155; padding:5px; border-radius:6px; }
            QSlider::groove:horizontal { height:6px; background:#1f2937; border-radius:3px; }
            QSlider::handle:horizontal { background:#3b82f6; width:14px; height:14px; margin:-4px 0; border-radius:7px; }
        """)

        main_layout = QtWidgets.QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(left_box)
        main_layout.addWidget(self.view, 1)

        # Menü
        menu = self.menuBar()
        m_file = menu.addMenu("Datei")
        act_csv = m_file.addAction("CSV laden…")
        act_csv.triggered.connect(self.on_load_csv)
        m_file.addSeparator()
        act_export = m_file.addAction("PNG exportieren…")
        act_export.triggered.connect(self.on_export_png)
        m_file.addSeparator()
        act_quit = m_file.addAction("Beenden")
        act_quit.triggered.connect(self.close)

    # ---------- Persistenz ----------

    def _settings(self) -> QtCore.QSettings:
        return QtCore.QSettings()

    def _save_last_csv(self, path: str) -> None:
        s = self._settings()
        s.setValue(self.SETTINGS_KEY_LAST_CSV, path)
        s.sync()
        debug(f"Zuletzt verwendete CSV gespeichert: {path}")

    def _load_last_csv_if_available(self) -> None:
        s = self._settings()
        path = s.value(self.SETTINGS_KEY_LAST_CSV, type=str)
        if path and os.path.isfile(path):
            try:
                debug(f"Letzte CSV gefunden, lade automatisch: {path}")
                self.csv_path = path
                self.df = load_mentions_csv(path)
                self._rebuild_graph()
            except Exception as ex:
                traceback.print_exc()
                QtWidgets.QMessageBox.warning(self, "Warnung", f"Letzte CSV konnte nicht geladen werden:\n{ex}")
        elif path:
            debug(f"Gespeicherter CSV-Pfad existiert nicht mehr: {path}")

    # ---------- Graph an JS ----------

    def _send_graph_to_js(self, nodes: List[dict], edges: List[dict]) -> None:
        payload = {
            "nodes": nodes,
            "edges": edges,
            "layoutName": self.current_layout,
            "showLabels": self.show_labels,
            "minWeight": self.min_w
        }
        js = f"window.setGraph({json.dumps(payload)})"
        self.view.run_js(js, lambda ok: debug(f"JS setGraph ok={ok}"))

    # ---------- Aktionen ----------

    def _rebuild_graph(self) -> None:
        if self.df is None:
            return
        try:
            built = build_bipartite_graph(self.df)
            self._send_graph_to_js(built.nodes, built.edges)
            title_suffix = f" — {os.path.basename(self.csv_path)}" if self.csv_path else ""
            inv = " [Invertierter Klick-Fokus]" if self.chk_invert_focus.isChecked() else ""
            self.setWindowTitle(f"chroniken_library↔Werke — Interaktives Netz{title_suffix}{inv}")
        except Exception as ex:
            traceback.print_exc()
            QtWidgets.QMessageBox.critical(self, "Fehler", f"Netzaufbau fehlgeschlagen:\n{ex}")

    def on_load_csv(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Mentions-CSV öffnen", os.getcwd(), "CSV Dateien (*.csv)")
        if not path:
            return
        try:
            self.csv_path = path
            self.df = load_mentions_csv(path)
            self._rebuild_graph()
            self._save_last_csv(path)
        except Exception as ex:
            traceback.print_exc()
            QtWidgets.QMessageBox.critical(self, "Fehler", f"CSV konnte nicht geladen werden:\n{ex}")

    def on_layout_change(self, text: str) -> None:
        self.current_layout = text
        self.view.run_js(f"window.setLayout({json.dumps(text)})", lambda ok: debug(f"JS setLayout ok={ok}"))

    def on_slider(self, value: int) -> None:
        self.min_w = int(value)
        self.lbl_thresh.setText(f"Kantenschwelle: ≥ {self.min_w}")
        self.view.run_js(f"window.setThreshold({int(value)})", lambda ok: debug(f"JS setThreshold ok={ok}"))

    def on_labels_toggle(self, state: int) -> None:
        self.show_labels = state == QtCore.Qt.Checked
        self.view.run_js(f"window.toggleLabels({str(self.show_labels).lower()})", lambda ok: debug(f"JS toggleLabels ok={ok}"))

    def on_invert_focus_toggle(self, state: int) -> None:
        on = (state == QtCore.Qt.Checked)
        self.view.run_js(f"window.setInvertFocus({str(on).lower()})", lambda ok: debug(f"JS setInvertFocus ok={ok}"))
        title_suffix = f" — {os.path.basename(self.csv_path)}" if self.csv_path else ""
        inv = " [Invertierter Klick-Fokus]" if on else ""
        self.setWindowTitle(f"chroniken_library↔Werke — Interaktives Netz{title_suffix}{inv}")

    def on_search(self) -> None:
        term = self.search_edit.text().strip()
        code = f"window.applySearch({json.dumps(term)})"
        def _cb(count):
            debug(f"Suche '{term}': {count} Treffer")
            if term and int(count) == 0:
                QtWidgets.QToolTip.showText(self.mapToGlobal(self.search_edit.pos()), "Kein Treffer", self.search_edit)
        self.view.run_js(code, _cb)

    def on_export_png(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "PNG exportieren", os.path.join(os.getcwd(), "chroniken_net.png"), "PNG (*.png)")
        if not path:
            return
        def _save(data_url: str) -> None:
            try:
                if not data_url or not data_url.startswith("data:image/png;base64,"):
                    raise ValueError("Ungültige PNG-Daten vom Renderer.")
                import base64
                raw = data_url.split(",", 1)[1]
                png = base64.b64decode(raw)
                with open(path, "wb") as f:
                    f.write(png)
                debug(f"PNG exportiert: {path}")
            except Exception as ex:
                traceback.print_exc()
                QtWidgets.QMessageBox.critical(self, "Fehler", f"PNG-Export scheiterte:\n{ex}")
        self.view.run_js("window.exportPng()", _save)


# ------------------------ Start ------------------------

def main() -> None:
    debug("Starte GUI …")
    # High-DPI
    try:
        QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
        QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
    except Exception:
        pass

    app = QtWidgets.QApplication(sys.argv)
    QtCore.QCoreApplication.setOrganizationName("chroniken_library")
    QtCore.QCoreApplication.setApplicationName("ChronikenWerkeCytoscape")

    # Minimaler Sanity-Check: QtWebEngine vorhanden?
    try:
        _ = QtWebEngineWidgets.QWebEngineView  # type: ignore
    except Exception as ex:
        QtWidgets.QMessageBox.critical(None, "Fehler", f"QtWebEngine fehlt:\n{ex}\nInstalliere: pip install PyQtWebEngine")
        sys.exit(2)

    win = MainWindow()
    # Optional: Auto-Ladesuche
    if win.df is None:
        # Versuche Standardpfade
        cands = [
            os.environ.get("CHRONIKEN_MENTIONS_CSV", "").strip(),
            os.path.join(os.getcwd(), "chroniken_mentions.csv"),
            os.path.expanduser("~/PycharmProjects/Find_Bibliography_NEw/data/azk_library/chroniken_mentions.csv"),
        ]
        for c in cands:
            if c and os.path.isfile(c):
                try:
                    win.csv_path = c
                    win.df = load_mentions_csv(c)
                    win._rebuild_graph()
                    win._save_last_csv(c)
                    break
                except Exception as ex:
                    debug(f"Auto-Laden scheiterte: {ex}")

    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()