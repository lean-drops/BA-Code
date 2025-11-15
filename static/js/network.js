
// Baut vis-network, bindet Filter-UI, lädt JSON vom Server-Endpunkt in #graph-endpoint.
(function () {
  "use strict";

  function fetchEndpoint() {
    var tag = document.getElementById("graph-endpoint");
    return tag ? (tag.textContent || "").trim().replace(/^"|"$/g, "") : "";
  }

  function neighborsWithin(adj, start, radius) {
    if (!radius || radius <= 0) return null;
    var vis = new Set([start]);
    var frontier = new Set([start]);
    for (var r = 0; r < radius; r++) {
      var nxt = new Set();
      frontier.forEach(function (u) {
        (adj.get(u) || []).forEach(function (v) { if (!vis.has(v)) { vis.add(v); nxt.add(v); } });
      });
      frontier = nxt;
      if (frontier.size === 0) break;
    }
    return vis;
  }

  function buildAdjacency(edges) {
    var adj = new Map();
    function add(a, b) { if (!adj.has(a)) adj.set(a, new Set()); adj.get(a).add(b); }
    edges.forEach(function (e) { add(e.from, e.to); add(e.to, e.from); });
    return adj;
  }

  function createNetwork(data) {
    var container = document.getElementById("network");
    var nodes = new vis.DataSet((data.nodes || []).map(function (n) {
      return {
        id: n.id, label: n.label, color: n.color, value: n.size,
        title: n.label + " | in=" + n.in_w + " out=" + n.out_w + " | " + (n.layer || "unknown")
      };
    }));
    var edges = new vis.DataSet((data.edges || []).map(function (e, idx) {
      return {
        id: e.id || (e.from + "→" + e.to + "#" + idx),
        from: e.from, to: e.to, arrows: "to", width: e.width,
        color: e.color || undefined, title: e.title,
        edge_type: e.edge_type || "AC", source_type: e.source_type || "",
        weight: e.weight || 0, smooth: { type: "dynamic" }
      };
    }));
    var network = new vis.Network(container, { nodes: nodes, edges: edges }, {
      physics: {
        stabilization: true,
        barnesHut: { gravitationalConstant: -8000, centralGravity: 0.2, springLength: 140, springConstant: 0.02 }
      },
      interaction: { hover: true, tooltipDelay: 100, zoomView: true },
      nodes: { shape: "dot", borderWidth: 0.5 },
      edges: { selectionWidth: 1.5 }
    });
    return { network, nodes, edges };
  }

  function bindControls(ctx, data) {
    var network = ctx.network, nodes = ctx.nodes, edges = ctx.edges;
    var minWeight = document.getElementById("minWeight");
    var searchNode = document.getElementById("searchNode");
    var resetBtn = document.getElementById("resetBtn");
    var radiusSel = document.getElementById("radiusSel");
    var chkAA = document.getElementById("chkAA");
    var chkAC = document.getElementById("chkAC");
    var chkCC = document.getElementById("chkCC");

    var adj = buildAdjacency(data.edges || []);
    var state = {
      minW: parseFloat(minWeight ? (minWeight.value || "0") : "0"),
      radius: parseInt(radiusSel ? (radiusSel.value || "0") : "0", 10) || 0,
      allowedTypes: new Set(["AA","AC","CC"]),
      focusSet: null
    };

    function updateAllowedTypes() {
      var s = new Set();
      if (!chkAA || chkAA.checked) s.add("AA");
      if (!chkAC || chkAC.checked) s.add("AC");
      if (!chkCC || chkCC.checked) s.add("CC");
      state.allowedTypes = s;
    }

    function applyFilters() {
      edges.forEach(function (e) {
        var ed = edges.get(e.id);
        var typeOk = state.allowedTypes.has(ed.edge_type);
        var wOk = (ed.weight || 0) >= state.minW;
        var nbOk = true;
        if (state.focusSet) nbOk = state.focusSet.has(ed.from) && state.focusSet.has(ed.to);
        edges.update({ id: ed.id, hidden: !(typeOk && wOk && nbOk) });
      });
      var visibleIds = new Set();
      edges.forEach(function (e) {
        var ed = edges.get(e.id);
        if (!ed.hidden) { visibleIds.add(ed.from); visibleIds.add(ed.to); }
      });
      nodes.forEach(function (n) {
        var hide = false;
        if (state.focusSet) hide = !state.focusSet.has(n.id);
        if (!hide && visibleIds.size > 0) hide = !visibleIds.has(n.id);
        nodes.update({ id: n.id, hidden: hide });
      });
    }

    function applyMinWeight() {
      state.minW = parseFloat(minWeight.value || "0");
      applyFilters();
    }

    function doSearchFocus() {
      var q = (searchNode.value || "").toLowerCase();
      if (!q) return;
      var found = (data.nodes || []).find(function (n) {
        return n.id.toLowerCase() === q || (n.label || "").toLowerCase().indexOf(q) >= 0;
      });
      if (found) {
        network.selectNodes([found.id]);
        network.focus(found.id, { scale: 1.2, animation: { duration: 600, easingFunction: "easeInOutCubic" } });
      }
    }

    function updateRadius() {
      state.radius = parseInt(radiusSel.value || "0", 10) || 0;
      var sel = network.getSelectedNodes();
      if (sel && sel.length > 0 && state.radius > 0) {
        state.focusSet = neighborsWithin(adj, sel[0], state.radius);
      } else {
        state.focusSet = null;
      }
      applyFilters();
    }

    function resetAll() {
      if (minWeight) minWeight.value = "0";
      if (radiusSel) radiusSel.value = "0";
      if (chkAA) chkAA.checked = true;
      if (chkAC) chkAC.checked = true;
      if (chkCC) chkCC.checked = true;
      state.minW = 0; state.radius = 0; state.focusSet = null;
      updateAllowedTypes();
      edges.forEach(function (e) { edges.update({ id: e.id, hidden: false }); });
      nodes.forEach(function (n) { nodes.update({ id: n.id, hidden: false }); });
      network.fit({ animation: { duration: 600 } });
      if (searchNode) searchNode.value = "";
      network.unselectAll();
    }

    if (minWeight) minWeight.addEventListener("change", applyMinWeight);
    if (searchNode) searchNode.addEventListener("keydown", function (ev) { if (ev.key === "Enter") doSearchFocus(); });
    if (resetBtn) resetBtn.addEventListener("click", resetAll);
    if (radiusSel) radiusSel.addEventListener("change", updateRadius);
    if (chkAA) chkAA.addEventListener("change", function(){ updateAllowedTypes(); applyFilters(); });
    if (chkAC) chkAC.addEventListener("change", function(){ updateAllowedTypes(); applyFilters(); });
    if (chkCC) chkCC.addEventListener("change", function(){ updateAllowedTypes(); applyFilters(); });

    network.on("selectNode", function () { if (radiusSel) updateRadius(); });
    network.on("deselectNode", function () {
      if (!radiusSel) return;
      if ((radiusSel.value || "0") === "0") { state.focusSet = null; applyFilters(); }
    });

    updateAllowedTypes();
    applyFilters();
  }

  async function init() {
    var endpoint = fetchEndpoint();
    if (!endpoint) return;
    try {
      var res = await fetch(endpoint);
      var txt = await res.json();              // server liefert JSON-string
      var data = JSON.parse(txt);              // daher 2x
      var ctx = createNetwork(data);
      bindControls(ctx, data);
    } catch (e) {
      console.error("Laden fehlgeschlagen:", e);
      alert("Graph konnte nicht geladen werden.");
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();

