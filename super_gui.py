#!/usr/bin/env python3
"""
fix_network_frontend.py
Repariert das Frontend für das Autoren/Werke-Netz:
- Behebt das falsche Cytoscape-Elements-Shape in index.js (muss {nodes,edges} sein, nicht {elements:{…}}).
- Fügt robuste Styles ein: node[label] und node[mass], damit keine Mapping-Warnungen auftreten.
- Setzt einen sicheren Default für den Weight-Filter.
- Patcht index.html um einen lokalen Cytoscape-Fallback (/assets/cytoscape.min.js) falls noch nicht vorhanden.

Usage:
    python fix_network_frontend.py
Ann.: Template-Ordner: /Users/programming/PycharmProjects/Find_Bibliography_NEw/config/authors_report_template
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

TEMPLATE_DIR = Path("/Users/programming/PycharmProjects/Find_Bibliography_NEw/config/authors_report_template")
INDEX_JS = TEMPLATE_DIR / "index.js"
INDEX_HTML = TEMPLATE_DIR / "index.html"
CY_LOCAL = TEMPLATE_DIR / "cytoscape.min.js"  # optional, falls vorhanden wird Fallback gesetzt

NEW_INDEX_JS = r"""// index.js — fixed elements shape + robust styles
(() => {
  "use strict";

  const els = {
    file: document.getElementById("index-file"),
    upload: document.getElementById("index-upload"),
    autoload: document.getElementById("index-autoload"),
    variant: document.getElementById("index-variant"),
    minw: document.getElementById("index-minw"),
    minwVal: document.getElementById("index-minw-val"),
    labels: document.getElementById("index-labels"),
    fit: document.getElementById("index-fit"),
    reset: document.getElementById("index-reset"),
    fileinfo: document.getElementById("index-fileinfo"),
    statsN: document.getElementById("index-stats-n"),
    statsE: document.getElementById("index-stats-e"),
    statsV: document.getElementById("index-stats-v"),
    cy: document.getElementById("index-cy"),
  };

  if (typeof window.cytoscape === "undefined") {
    alert("Cytoscape nicht geladen"); throw new Error("Cytoscape missing");
  }

  /** @type {Cytoscape.Core|null} */
  let cy = null;
  let payload = null;
  let currentVariant = "bipartite";

  function setStats(n, e, variantName, filename) {
    els.statsN.textContent = String(n ?? 0);
    els.statsE.textContent = String(e ?? 0);
    els.statsV.textContent = variantName ?? "bipartite";
    if (filename) els.fileinfo.textContent = filename;
    console.debug("[frontend] stats", { n, e, variantName, filename });
  }

  // FIX: korrekte Elements-Struktur
  function buildElements(nodes, edges) {
    return { nodes: Array.isArray(nodes) ? nodes : [], edges: Array.isArray(edges) ? edges : [] };
  }

  function createCy(container, elements, showLabels) {
    if (cy) { cy.destroy(); cy = null; }
    cy = cytoscape({
      container,
      elements, // <- erwartet {nodes:[], edges:[]}
      layout: { name: "preset", fit: true },
      // Hinweis: wheelSensitivity default belassen für natürliches Zoom
      style: [
        // Grundstil für alle Nodes
        {
          selector: "node",
          style: {
            "background-color": "#5aa9ff",
            "border-color": "#c8d9ff",
            "border-opacity": 0.35,
            "border-width": 1,
            "width": 18,
            "height": 18,
            "label": "",
            "font-size": 10,
            "color": "#e7eaf0",
            "text-outline-color": "#0b0d12",
            "text-outline-width": 0,
            "text-valign": "center",
            "text-halign": "center",
            "opacity": 0.95
          }
        },
        // Label nur, wenn vorhanden
        {
          selector: "node[label]",
          style: {
            "label": showLabels ? "data(label)" : "",
            "text-outline-width": showLabels ? 2 : 0
          }
        },
        // Größe nur, wenn mass vorhanden
        {
          selector: "node[mass]",
          style: {
            "width": "mapData(mass, 1, 20, 10, 38)",
            "height": "mapData(mass, 1, 20, 10, 38)"
          }
        },
        // Farbakzent für chronik
        {
          selector: "node[role = 'chronik']",
          style: { "background-color": "#22cc88" }
        },
        // Edges
        {
          selector: "edge",
          style: {
            "line-color": "#7a8291",
            "opacity": 0.7,
            "width": "mapData(weight, 1, 20, 1, 6)"
          }
        }
      ]
    });
    cy.ready(() => cy.fit());
  }

  function applyMinWeight(threshold) {
    if (!cy) return;
    els.minwVal.textContent = String(threshold);
    cy.edges().forEach(e => {
      const w = Number(e.data("weight") ?? 0);
      e.style("display", w >= threshold ? "element" : "none");
    });
    // Isolierte Nodes ausblenden
    cy.nodes().forEach(n => {
      const hasVisibleEdge = n.connectedEdges(":visible").length > 0;
      n.style("display", hasVisibleEdge ? "element" : "none");
    });
  }

  function renderVariant(name) {
    if (!payload) return;
    const base = payload.variants && payload.variants[name] ? payload.variants[name] : payload;
    const nodes = base.nodes || payload.nodes || [];
    const edges = base.edges || payload.edges || [];
    const elements = buildElements(nodes, edges);
    createCy(els.cy, elements, els.labels.checked);
    setStats(nodes.length, edges.length, base.name || name || "bipartite", payload.filename || "–");
    applyMinWeight(Number(els.minw.value));
  }

  function populateVariants(pl) {
    const sel = els.variant;
    sel.innerHTML = "";
    const variants = pl.variants ? Object.keys(pl.variants) : ["bipartite"];
    for (const key of variants) {
      const opt = document.createElement("option");
      opt.value = key; opt.textContent = key; sel.appendChild(opt);
    }
    sel.value = variants.includes("bipartite") ? "bipartite" : variants[0];
  }

  async function doAutoload() {
    const res = await fetch("/api/autoload");
    if (!res.ok) { alert("Autoload fehlgeschlagen"); return; }
    payload = await res.json();
    populateVariants(payload);
    // Default-Threshold: 1
    if (els.minw) els.minw.value = "1";
    renderVariant(els.variant.value);
  }

  async function doUpload(file) {
    const fd = new FormData();
    fd.append("file", file, file.name);
    const res = await fetch("/api/upload_csv", { method: "POST", body: fd });
    if (!res.ok) { alert("Upload fehlgeschlagen"); return; }
    payload = await res.json();
    populateVariants(payload);
    if (els.minw) els.minw.value = "1";
    renderVariant(els.variant.value);
  }

  // Events
  els.autoload?.addEventListener("click", () => void doAutoload());
  els.upload?.addEventListener("click", () => {
    const f = els.file?.files && els.file.files[0];
    if (!f) { alert("Bitte CSV wählen."); return; }
    void doUpload(f);
  });
  els.variant?.addEventListener("change", () => renderVariant(els.variant.value));
  els.labels?.addEventListener("change", () => renderVariant(currentVariant));
  els.minw?.addEventListener("input", () => applyMinWeight(Number(els.minw.value)));
  els.fit?.addEventListener("click", () => cy && cy.fit());
  els.reset?.addEventListener("click", () => cy && cy.reset());

  // Auto-Init
  window.addEventListener("DOMContentLoaded", () => { doAutoload().catch(() => {}); });
})();
"""

def ensure_dir(p: Path) -> None:
    if not p.exists() or not p.is_dir():
        raise FileNotFoundError(f"Template-Ordner fehlt: {p}")

def backup(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    bak = path.with_suffix(path.suffix + ".bak")
    bak.write_bytes(path.read_bytes())
    print(f"[DEBUG] Backup geschrieben: {bak}")
    return bak

def write_index_js(path: Path) -> None:
    path.write_text(NEW_INDEX_JS, encoding="utf-8")
    print(f"[DEBUG] index.js ersetzt: {path}")

def patch_index_html_fallback(path: Path, have_local: bool) -> None:
    html = path.read_text(encoding="utf-8", errors="ignore")
    changed = False
    if "cytoscape.min.js" not in html or ("unpkg.com" not in html and "jsdelivr.net" not in html):
        html = re.sub(r"</head>", '<script src="https://unpkg.com/cytoscape@3.26.0/dist/cytoscape.min.js"></script>\n</head>',
                      html, count=1, flags=re.IGNORECASE)
        changed = True
    if 'id="CYTOSCAPE_FALLBACK"' not in html and have_local:
        snippet = "<script id=\"CYTOSCAPE_FALLBACK\">(function(){function I(s,c){var e=document.createElement('script');e.src=s;e.onload=c;document.head.appendChild(e);}if(!window.cytoscape){I('/assets/cytoscape.min.js',function(){if(!window.cytoscape){console.error('Cytoscape Fallback fehlgeschlagen');}});}})();</script>"
        html = re.sub(r"</head>", snippet + "\n</head>", html, count=1, flags=re.IGNORECASE)
        changed = True
    if changed:
        path.write_text(html, encoding="utf-8")
        print(f"[DEBUG] index.html gepatcht: {path}")

def main() -> None:
    print(f"[DEBUG] TEMPLATE_DIR: {TEMPLATE_DIR}")
    ensure_dir(TEMPLATE_DIR)
    if not INDEX_HTML.exists():
        raise FileNotFoundError(f"index.html fehlt: {INDEX_HTML}")

    # index.js ersetzen, robust gegen formale Fehler
    backup(INDEX_JS)
    write_index_js(INDEX_JS)

    # index.html Fallback ergänzen, wenn lokale Datei existiert
    patch_index_html_fallback(INDEX_HTML, CY_LOCAL.exists())

    print("[DEBUG] Fertig. Flask neu starten und Seite neu laden. Variante wählen und Fit klicken.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)