/**
 * report_ui_fast.js
 * Beschleunigte Filterung für Aggregat-/Detailtabellen mittels Inverted Index und Delta-DOM.
 * Drop-in-Replacement für das bisherige Skript.
 */
(function () {
  'use strict';

  /*** Utilities ***/
  const $  = (s, c = document) => c.querySelector(s);
  const $$ = (s, c = document) => Array.from(c.querySelectorAll(s));
  const t0 = () => performance.now();

  function norm(s) {
    return String(s || '')
      .toLowerCase()
      .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
      .replace(/ä/g, 'ae').replace(/ö/g, 'oe').replace(/ü/g, 'ue').replace(/ß/g, 'ss')
      .replace(/[^\p{L}\p{N}]+/gu, ' ')
      .replace(/[vu]/g, 'v').replace(/[ij]/g, 'i')
      .trim();
  }
  function tokensOf(s) {
    const out = new Set();
    for (const tok of norm(s).split(/\s+/)) if (tok.length > 1) out.add(tok);
    return out;
  }
  // edit distance <=1 inkl. Transposition
  function edit1(a, b) {
    if (a === b) return true;
    const la = a.length, lb = b.length;
    if (Math.abs(la - lb) > 1) return false;
    let i = 0, j = 0, ed = 0;
    while (i < la && j < lb) {
      if (a[i] === b[j]) { i++; j++; continue; }
      ed++; if (ed > 1) return false;
      if (la > lb) i++; else if (lb > la) j++;
      else {
        if (a[i + 1] === b[j] && a[i] === b[j + 1]) { i += 2; j += 2; } else { i++; j++; }
      }
    }
    if (i < la || j < lb) ed++;
    return ed <= 1;
  }

  /*** Dark mode ***/
  const darkBtn = $('#dark');
  function setDark(on) {
    document.documentElement.classList.toggle('dark', !!on);
    try { localStorage.setItem('chronik_dark', on ? '1' : '0'); } catch (e) {}
  }
  try { setDark(localStorage.getItem('chronik_dark') === '1'); } catch (e) {}
  if (darkBtn) darkBtn.onclick = () => setDark(!document.documentElement.classList.contains('dark'));

  /*** State ***/
  let activeGroup = null, activeLabel = null, activeFile = null;
  let aggQuery = '', detQuery = '';
  const fltAgg = $('#fltAgg'), fltDet = $('#fltDet');
  const clearDet = $('#clearDet'), clearGroups = $('#clearGroups'), clearLabels = $('#clearLabels');
  const aggCount = $('#aggCount'), detCount = $('#detCount');

  /*** Data structures ***/
  const aggTable = $('#agg'), detTable = $('#det');

  // Rows + IDs
  const aggRows = $$('#agg tbody tr').map((row, id) => {
    const g = row.dataset.group || '', l = row.dataset.label || '';
    const gn = norm(g), ln = norm(l);
    return { id, row, g, l, gn, ln, hay: `${gn} ${ln}` };
  });
  const detRows = $$('#det tbody tr').map((row, id) => {
    const tds = row.children;
    const file = tds[0]?.innerText || '', page = tds[1]?.innerText || '';
    const g = row.dataset.group || '', l = row.dataset.label || '';
    const pat = tds[4]?.innerText || '', ctx = tds[5]?.innerText || '';
    const fn = norm(file), pn = norm(page), gn = norm(g), ln = norm(l);
    const pan = norm(pat), cxn = norm(ctx);
    return { id, row, g, l, file, fn, pn, gn, ln, pan, cxn };
  });

  // Inverted Indices
  function buildIndex(rows, textOf) {
    /** @type {Map<string, Set<number>>} */
    const idx = new Map();
    /** @type {Map<string, Set<number>>} */
    const byGroup = new Map();
    /** @type {Map<string, Set<number>>} */
    const byLabel = new Map();
    /** @type {Map<string, Set<number>>} */
    const byFile = new Map();
    /** @type {Map<string, Set<number>>} only for pattern/context */
    const patCtx = new Map();

    // token buckets by first two chars to prune scans
    /** @type {Map<string, Set<string>>} */
    const buckets2 = new Map();

    const labelsVocab = new Set();
    const groupsVocab = new Set();
    const filesVocab = new Set();

    for (const r of rows) {
      // label/group/file indices
      if (r.gn) { if (!byGroup.has(r.gn)) byGroup.set(r.gn, new Set()); byGroup.get(r.gn).add(r.id); groupsVocab.add(r.gn); }
      if (r.ln) { if (!byLabel.has(r.ln)) byLabel.set(r.ln, new Set()); byLabel.get(r.ln).add(r.id); labelsVocab.add(r.ln); }
      if ('fn' in r && r.fn) { if (!byFile.has(r.fn)) byFile.set(r.fn, new Set()); byFile.get(r.fn).add(r.id); filesVocab.add(r.fn); }

      // all-text tokens
      const toks = tokensOf(textOf(r));
      for (const tok of toks) {
        if (!idx.has(tok)) idx.set(tok, new Set());
        idx.get(tok).add(r.id);
        const b = tok.slice(0, 2);
        if (!buckets2.has(b)) buckets2.set(b, new Set());
        buckets2.get(b).add(tok);
      }

      // pattern/context tokens for details
      if ('pan' in r || 'cxn' in r) {
        const toksPC = tokensOf(`${r.pan || ''} ${r.cxn || ''}`);
        for (const tok of toksPC) {
          if (!patCtx.has(tok)) patCtx.set(tok, new Set());
          patCtx.get(tok).add(r.id);
        }
      }
    }

    return {
      idx,
      byGroup, byLabel, byFile,
      buckets2,
      labelsVocab: Array.from(labelsVocab),
      groupsVocab: Array.from(groupsVocab),
      filesVocab: Array.from(filesVocab),
      patCtx
    };
  }

  const AGG = buildIndex(aggRows, r => r.hay);
  const DET = buildIndex(detRows, r => `${r.fn} ${r.ln} ${r.gn} ${r.pan} ${r.cxn}`);

  // fast helpers for set ops
  const toSet = a => (a instanceof Set ? a : new Set(a));
  function setClone(s) { return new Set(s); }
  function setUnion(a, b) { const out = new Set(a); for (const x of b) out.add(x); return out; }
  function setInter(a, b) {
    if (!a || !b) return new Set();
    const small = a.size <= b.size ? a : b;
    const big = a.size <= b.size ? b : a;
    const out = new Set();
    for (const x of small) if (big.has(x)) out.add(x);
    return out;
  }
  function setDiff(a, b) { const out = new Set(); for (const x of a) if (!b.has(x)) out.add(x); return out; }

  // token -> rows via substring using 2-char buckets
  function rowsBySubstring(index, buckets2, term) {
    const t = norm(term);
    if (!t) return new Set();
    const candTokens = buckets2.get(t.slice(0, 2)) || new Set(index.keys());
    let out = new Set();
    for (const tok of candTokens) {
      if (tok.includes(t)) out = setUnion(out, index.get(tok));
    }
    return out;
  }
  function rowsByKeySubstring(map, vocabArr, term) {
    const t = norm(term);
    if (!t) return new Set();
    const out = new Set();
    for (const key of vocabArr) if (key.includes(t)) for (const id of map.get(key)) out.add(id);
    return out;
  }
  function rowsByLabelFuzzy(map, vocabArr, q) {
    const t = norm(q);
    if (!t) return new Set();
    const out = new Set();
    for (const key of vocabArr) {
      if (key.includes(t) || edit1(key, t)) for (const id of map.get(key)) out.add(id);
    }
    return out;
  }

  /*** Query parser ***/
  function parseQuery(q) {
    const out = { group: null, label: null, file: null, pattern: null, text: [] };
    const toks = String(q || '').trim().split(/\s+/).filter(Boolean);
    for (const t of toks) {
      const m = t.match(/^(\w+):(.*)$/);
      if (m) {
        const k = m[1].toLowerCase(), v = norm(m[2]);
        if (k === 'group') out.group = v;
        else if (k === 'label') out.label = v;
        else if (k === 'file') out.file = v;
        else if (k === 'pattern') out.pattern = v;
        else out.text.push(norm(t));
      } else out.text.push(norm(t));
    }
    return out;
  }

  /*** Visible sets and DOM patcher ***/
  let visAgg = new Set(aggRows.map(r => r.id));
  let visDet = new Set(detRows.map(r => r.id));

  function patchVisibility(rows, prev, next, countEl) {
    const hide = setDiff(prev, next);
    const show = setDiff(next, prev);
    for (const id of hide) rows[id].row.classList.add('hidden');
    for (const id of show) rows[id].row.classList.remove('hidden');
    if (countEl) countEl.textContent = String(next.size);
    return next;
  }

  /*** Search core ***/
  function searchAgg(q) {
    const tStart = t0();
    if (!q && !activeGroup && !activeLabel) return new Set(aggRows.map(r => r.id));

    const pq = parseQuery(q);
    let cand = null;

    // Start with strongest restriction available
    if (activeGroup && AGG.byGroup.has(activeGroup)) cand = setClone(AGG.byGroup.get(activeGroup));
    if (activeLabel && AGG.byLabel.has(activeLabel)) cand = cand ? setInter(cand, AGG.byLabel.get(activeLabel)) : setClone(AGG.byLabel.get(activeLabel));

    if (pq.group) {
      const s = rowsByKeySubstring(AGG.byGroup, AGG.groupsVocab, pq.group);
      cand = cand ? setInter(cand, s) : s;
    }
    if (pq.label) {
      const s = rowsByLabelFuzzy(AGG.byLabel, AGG.labelsVocab, pq.label);
      cand = cand ? setInter(cand, s) : s;
    }
    for (const t of pq.text) {
      const s = rowsBySubstring(AGG.idx, AGG.buckets2, t);
      cand = cand ? setInter(cand, s) : s;
    }
    // Fallback: wenn nichts einschränkt, alles
    const out = cand || new Set(aggRows.map(r => r.id));
    console.debug('[agg] ms=', (t0() - tStart).toFixed(1), 'size=', out.size);
    return out;
  }

  function searchDet(q) {
    const tStart = t0();
    if (!q && !activeGroup && !activeLabel && !activeFile) return new Set(detRows.map(r => r.id));

    const pq = parseQuery(q);
    let cand = null;

    if (activeGroup && DET.byGroup.has(activeGroup)) cand = setClone(DET.byGroup.get(activeGroup));
    if (activeLabel && DET.byLabel.has(activeLabel)) cand = cand ? setInter(cand, DET.byLabel.get(activeLabel)) : setClone(DET.byLabel.get(activeLabel));
    if (activeFile && DET.byFile.has(norm(activeFile))) cand = cand ? setInter(cand, DET.byFile.get(norm(activeFile))) : setClone(DET.byFile.get(norm(activeFile)));

    if (pq.group) {
      const s = rowsByKeySubstring(DET.byGroup, DET.groupsVocab, pq.group);
      cand = cand ? setInter(cand, s) : s;
    }
    if (pq.label) {
      const s = rowsByKeySubstring(DET.byLabel, DET.labelsVocab, pq.label); // Details: includes reicht
      cand = cand ? setInter(cand, s) : s;
    }
    if (pq.file) {
      const s = rowsByKeySubstring(DET.byFile, DET.filesVocab, pq.file);
      cand = cand ? setInter(cand, s) : s;
    }
    if (pq.pattern) {
      const s = rowsBySubstring(DET.patCtx, DET.buckets2, pq.pattern);
      cand = cand ? setInter(cand, s) : s;
    }
    for (const t of pq.text) {
      const s = rowsBySubstring(DET.idx, DET.buckets2, t);
      cand = cand ? setInter(cand, s) : s;
    }

    const out = cand || new Set(detRows.map(r => r.id));
    console.debug('[det] ms=', (t0() - tStart).toFixed(1), 'size=', out.size);
    return out;
  }

  /*** Decorations: map group color to .lbl if stats exist ***/
  (function decorateByGroup() {
    const stats = (window.LABEL_STATS || {});
    $$('#agg .lbl, #det .lbl').forEach(b => {
      const s = stats[b.dataset.label];
      if (s && s.group) b.dataset.group = String(s.group);
    });
  })();

  /*** Bindings ***/
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
  function applyAgg() { visAgg = patchVisibility(aggRows, visAgg, searchAgg(aggQuery), aggCount); }
  function applyDet() { visDet = patchVisibility(detRows, visDet, searchDet(detQuery), detCount); }

  if (fltAgg) fltAgg.oninput = debounce(() => { aggQuery = fltAgg.value || ''; applyAgg(); }, 120);
  if (fltDet) fltDet.oninput = debounce(() => { detQuery = fltDet.value || ''; applyDet(); }, 120);
  if (clearDet) clearDet.onclick = () => { detQuery = ''; if (fltDet) fltDet.value = ''; applyDet(); };

  function selectLabel(label) {
    activeLabel = label;
    $$('.chip.label').forEach(c => c.classList.toggle('active', c.dataset.label === activeLabel));
    applyAgg(); applyDet();
  }
  function selectFile(file) {
    activeFile = file;
    $$('.chip.file').forEach(c => c.classList.toggle('active', c.dataset.file === activeFile));
    applyDet();
  }

  $$('.chip.group').forEach(ch => {
    ch.addEventListener('click', () => {
      const g = norm(ch.dataset.group || '');
      activeGroup = (activeGroup === g) ? null : g;
      $$('.chip.group').forEach(c => c.classList.toggle('active', norm(c.dataset.group || '') === activeGroup));
      applyAgg(); applyDet();
    });
  });
  if (clearGroups) clearGroups.onclick = () => {
    activeGroup = null;
    $$('.chip.group').forEach(c => c.classList.remove('active'));
    applyAgg(); applyDet();
  };

  $$('.chip.file').forEach(ch => ch.addEventListener('click', () => selectFile(ch.dataset.file)));
  if (clearLabels) clearLabels.onclick = () => { selectLabel(null); selectFile(null); };

  $$('#agg .lbl, #det .lbl').forEach(b => b.addEventListener('click', () => selectLabel(b.dataset.label)));

  /*** Init ***/
  // Start fully visible, counts setzen und dann erste Filter anwenden
  if (aggCount) aggCount.textContent = String(aggRows.length);
  if (detCount) detCount.textContent = String(detRows.length);
  applyAgg(); applyDet();

})();