
/* csv_pipeline.js
   Liest Autoren- und Chroniken-CSV im Browser, repliziert die Python-Heuristiken
   und erzeugt {nodes, edges} Payload für vis-network sowie CSV-Downloads.
   Keine externen Dependencies.
*/
(function (global) {
  "use strict";

  // ---------- Utils ----------
  function readFileAsText(file) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onerror = () => reject(r.error);
      r.onload = () => resolve(String(r.result || ""));
      r.readAsText(file, "utf-8");
    });
  }

  function sniffDelimiter(text) {
    const head = text.split(/\r?\n/).slice(0, 5).join("\n");
    const semi = (head.match(/;/g) || []).length;
    const comma = (head.match(/,/g) || []).length;
    return semi > comma ? ";" : ",";
  }

  function stripBOM(s) {
    return s.charCodeAt(0) === 0xfeff ? s.slice(1) : s;
  }

  // Minimaler CSV-Parser mit Quote-Unterstützung (", ""-Escapes)
  function parseCSV(text, sep) {
    text = stripBOM(String(text || ""));
    sep = sep || ",";
    const rows = [];
    let i = 0, field = "", inQuotes = false, row = [];
    while (i < text.length) {
      const ch = text[i];
      if (inQuotes) {
        if (ch === '"') {
          if (text[i + 1] === '"') { field += '"'; i += 2; continue; }
          inQuotes = false; i++; continue;
        }
        field += ch; i++; continue;
      } else {
        if (ch === '"') { inQuotes = true; i++; continue; }
        if (ch === sep) { row.push(field); field = ""; i++; continue; }
        if (ch === "\n") { row.push(field); rows.push(row); row = []; field = ""; i++; continue; }
        if (ch === "\r") { // handle CRLF
          if (text[i + 1] === "\n") { i++; }
          row.push(field); rows.push(row); row = []; field = ""; i++; continue;
        }
        field += ch; i++; continue;
      }
    }
    // Push last field/row
    row.push(field); rows.push(row);
    // Drop trailing empty row if file ends with newline
    if (rows.length && rows[rows.length - 1].length === 1 && rows[rows.length - 1][0] === "") {
      rows.pop();
    }
    // Header + records
    if (!rows.length) return { header: [], records: [] };
    const header = rows[0].map(h => h.trim());
    const records = rows.slice(1).map(r => {
      const o = {};
      for (let j = 0; j < header.length; j++) o[header[j]] = (r[j] || "").trim();
      return o;
    });
    return { header, records };
  }

  // Slugify und Primärtokens wie in Python
  const _slugRx = /[^a-z0-9]+/g;
  const _combining = /[\u0300-\u036f]/g;
  const _particles = new Set(["von", "van", "de", "der", "den", "di", "da", "le", "la", "du", "del", "della", "zu"]);
  const _generic = new Set([
    "werk","werke","chronik","chroniken","chronicon","geschichte","historie","lexikon","annales","schrift","schriften",
    "sammlung","jahrbuch","edition","tigurinerchronik","eidgenossische","eidgenössische","schweizerchronik","band","teil","buch","capell","reformationsgeschichte"
  ]);

  function normalizeNFD(s) {
    return s.normalize ? s.normalize("NFKD") : s;
  }

  function slugify(s) {
    s = String(s || "").trim().toLowerCase();
    s = normalizeNFD(s).replace(_combining, "");
    s = s.replace(_slugRx, "-").replace(/^-+|-+$/g, "");
    return s;
  }

  function isRoman(t) { return /^[ivxlcdm]+$/.test(t); }

  function primaryTokenFromSlug(slug) {
    if (!slug) return slug;
    const parts = slug.split("-").filter(Boolean);
    if (!parts.length) return slug;
    for (let i = parts.length - 1; i >= 0; i--) {
      const t = parts[i];
      if (_particles.has(t) || _generic.has(t) || isRoman(t) || /^\d+$/.test(t)) continue;
      return t;
    }
    return parts[0];
  }

  function basename(p) {
    p = String(p || "");
    // handle / and \ and possible file: URIs
    const m = p.match(/[^\/\\]+$/);
    return m ? m[0] : p;
  }

  function toInt(x) {
    const n = Number(String(x).replace(",", "."));
    return Number.isFinite(n) ? Math.round(n) : 0;
  }

  function pickWeightColumn(header, candidates) {
    for (const c of candidates) if (header.includes(c)) return c;
    throw new Error("Keine geeignete Gewichtsspalte gefunden. Erwartet eine aus " + candidates.join(", "));
  }

  // ---------- Autoren-Edges ----------
  function buildAuthorsEdges(records, header) {
    const need = ["src", "tgt"];
    for (const n of need) if (!header.includes(n)) throw new Error("Spalte fehlt in Autoren-CSV: " + n);
    const weightCol = pickWeightColumn(header, ["weighted", "total", "bib_hits", "text_hits"]);
    const agg = new Map(); // key=u\tv -> {u,v,weight,example}
    for (const r of records) {
      const src = r["src"];
      const tgt = r["tgt"];
      const w = toInt(r[weightCol]);
      const srcFam = primaryTokenFromSlug(slugify(src));
      const tgtFam = primaryTokenFromSlug(slugify(tgt));
      const u = tgtFam;  // cited → citer  == tgt → src
      const v = srcFam;
      if (!u || !v || u === v) continue;
      const key = u + "\t" + v;
      if (!agg.has(key)) agg.set(key, { u, v, weight: 0, example: "" });
      agg.get(key).weight += w;
      // first example if exists
      if (!agg.get(key).example && header.includes("examples")) {
        const ex = String(r["examples"] || "");
        agg.get(key).example = ex.length > 220 ? (ex.slice(0, 220) + "…") : ex;
      }
    }
    return Array.from(agg.values()).map(e => ({ ...e, source_type: "authors" }));
  }

  // ---------- Chroniken-Edges ----------
  function buildChronikenEdges(records, header) {
    const need = ["pdf_file", "label"];
    for (const n of need) if (!header.includes(n)) throw new Error("Spalte fehlt in Chroniken-CSV: " + n);

    // label_family + doc_id
    const rows = records.map(r => {
      const labelClean = String(r["label"] || "").replace(/\(.*?\)/g, "");
      const labelFam = primaryTokenFromSlug(slugify(labelClean));
      const docId = slugify(basename(r["pdf_file"] || ""));
      return {
        doc_id: docId,
        label_family: labelFam,
        context: String(r["context"] || "")
      };
    });

    // Hauptlabel je doc
    const counts = new Map(); // key=doc\tlabel -> count
    const perDocLabels = new Map(); // doc -> Set(labels)
    for (const r of rows) {
      const key = r.doc_id + "\t" + r.label_family;
      counts.set(key, (counts.get(key) || 0) + 1);
      if (!perDocLabels.has(r.doc_id)) perDocLabels.set(r.doc_id, new Set());
      perDocLabels.get(r.doc_id).add(r.label_family);
    }
    const mainByDoc = new Map();
    for (const [doc, labels] of perDocLabels.entries()) {
      let best = null, bestCnt = -1;
      const sorted = Array.from(labels).sort(); // Tie-Breaker alphabetisch
      for (const lab of sorted) {
        const c = counts.get(doc + "\t" + lab) || 0;
        if (c > bestCnt) { best = lab; bestCnt = c; }
      }
      mainByDoc.set(doc, best);
    }

    // Edges: anderes_label -> main_label
    const agg = new Map(); // key=u\tv -> {u,v,weight,context}
    for (const r of rows) {
      const main = mainByDoc.get(r.doc_id);
      if (!main || r.label_family === main) continue;
      const u = r.label_family, v = main;
      if (!u || !v || u === v) continue;
      const key = u + "\t" + v;
      if (!agg.has(key)) agg.set(key, { u, v, weight: 0, context: "" });
      agg.get(key).weight += 1;
      if (!agg.get(key).context && r.context) {
        const ctx = String(r.context);
        agg.get(key).context = ctx.length > 200 ? (ctx.slice(0, 200) + "…") : ctx;
      }
    }
    return Array.from(agg.values()).map(e => ({ ...e, source_type: "chroniken" }));
  }

  // ---------- Kombinieren, Layer, Typen ----------
  function buildPayload(edgesAuth, edgesChron) {
    const perSource = []; // u,v,weight,source_type, total_weight (später), edge_type (später)
    edgesAuth.forEach(e => perSource.push({ ...e }));
    edgesChron.forEach(e => perSource.push({ ...e }));

    // Menge der Knoten je Layer
    const setAuth = new Set(edgesAuth.flatMap(e => [e.u, e.v]));
    const setChron = new Set(edgesChron.flatMap(e => [e.u, e.v]));
    function layerOf(n) {
      const a = setAuth.has(n), c = setChron.has(n);
      if (a && c) return "both";
      if (a) return "modern_author";
      if (c) return "chron_label";
      return "unknown";
    }

    // totals je (u,v)
    const totals = new Map(); // key=u\tv -> total_weight
    for (const e of perSource) {
      const key = e.u + "\t" + e.v;
      totals.set(key, (totals.get(key) || 0) + toInt(e.weight));
    }
    for (const e of perSource) e.total_weight = totals.get(e.u + "\t" + e.v) || toInt(e.weight);

    function edgeType(u, v) {
      const lu = layerOf(u), lv = layerOf(v);
      if (lu === "modern_author" && lv === "modern_author") return "AA";
      if (lu === "chron_label" && lv === "chron_label") return "CC";
      return "AC";
    }
    for (const e of perSource) e.edge_type = edgeType(e.u, e.v);

    // Graph-Edges: eine Kante je (u,v) mit total_weight
    const visEdges = new Map(); // key -> {from,to,weight,...}
    for (const e of perSource) {
      const key = e.u + "\t" + e.v;
      if (!visEdges.has(key)) {
        visEdges.set(key, {
          from: e.u, to: e.v,
          weight: e.total_weight,
          edge_type: e.edge_type,
          source_type: e.source_type,
          title: `${e.u} → ${e.v} | type=${e.edge_type} | total=${e.total_weight} | src=${e.source_type}`,
        });
      } else {
        // Quellenliste zusammenführen
        const ex = visEdges.get(key);
        ex.source_type = Array.from(new Set(String(ex.source_type).split(",").concat([e.source_type]))).join(",");
      }
    }

    // Node-Degrees (gewichtete in/out)
    const inW = new Map(), outW = new Map();
    for (const ed of visEdges.values()) {
      const w = toInt(ed.weight);
      outW.set(ed.from, (outW.get(ed.from) || 0) + w);
      inW.set(ed.to, (inW.get(ed.to) || 0) + w);
    }

    const allNodes = new Set();
    for (const ed of visEdges.values()) { allNodes.add(ed.from); allNodes.add(ed.to); }
    const nodes = Array.from(allNodes).map(n => {
      const layer = layerOf(n);
      const iw = inW.get(n) || 0, ow = outW.get(n) || 0;
      return {
        id: n,
        label: n.charAt(0).toUpperCase() + n.slice(1),
        in_w: iw,
        out_w: ow,
        layer
      };
    });

    // vis-edge Cosmetics
    const EDGE_COLORS = { "AA": "#4C78A8", "AC": "#9C755F", "CC": "#F58518" };
    const edges = Array.from(visEdges.values()).map(e => ({
      ...e,
      width: Math.max(1.0, 0.6 * Math.log1p(toInt(e.weight))),
      color: EDGE_COLORS[e.edge_type] || "#888888"
    }));

    // CSVs
    const edgesCsv = toCSV(
      ["u","v","weight","source_type","total_weight","edge_type"],
      perSource.map(e => [e.u, e.v, toInt(e.weight), e.source_type, toInt(e.total_weight), e.edge_type])
        .sort((a,b)=> (b[4]-a[4]) || (b[2]-a[2]))
    );
    const nodesCsv = toCSV(
      ["node","layer","in_weight","out_weight"],
      nodes.map(n => [n.id, n.layer, toInt(n.in_w), toInt(n.out_w)])
        .sort((a,b)=> (b[2]-a[2]) || (b[3]-a[3]))
    );

    // vis-network Payload
    const LAYER_COLORS = { "modern_author":"#4C78A8","chron_label":"#F58518","both":"#54A24B","unknown":"#999999" };
    const payload = {
      nodes: nodes.map(n => ({
        id: n.id,
        label: n.label,
        in_w: n.in_w,
        out_w: n.out_w,
        layer: n.layer,
        color: LAYER_COLORS[n.layer] || "#999999",
        size: 10 + Math.log1p((n.in_w||0) + (n.out_w||0)) * 6.0
      })),
      edges: edges
    };
    return { payload, edgesCsv, nodesCsv };
  }

  function toCSV(header, rows) {
    function esc(v) {
      const s = String(v == null ? "" : v);
      return /[",\n;]/.test(s) ? '"' + s.replace(/"/g,'""') + '"' : s;
    }
    const h = header.map(esc).join(",");
    const body = rows.map(r => r.map(esc).join(",")).join("\n");
    return h + "\n" + body + "\n";
  }

  // ---------- Public API ----------
  async function processFiles(authorsFile, chronikenFile) {
    if (!authorsFile || !chronikenFile) throw new Error("Beide CSV-Dateien auswählen.");
    const [aText, cText] = await Promise.all([readFileAsText(authorsFile), readFileAsText(chronikenFile)]);
    const aSep = sniffDelimiter(aText), cSep = sniffDelimiter(cText);
    const aCsv = parseCSV(aText, aSep), cCsv = parseCSV(cText, cSep);
    const aEdges = buildAuthorsEdges(aCsv.records, aCsv.header);
    const cEdges = buildChronikenEdges(cCsv.records, cCsv.header);
    return buildPayload(aEdges, cEdges);
  }

  global.CSVPipeline = { processFiles };

})(window);

