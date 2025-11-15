// index.js — fixed elements shape + robust styles
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
