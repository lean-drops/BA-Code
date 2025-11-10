(function(){
  const $  = (s, c=document) => c.querySelector(s);
  const $$ = (s, c=document) => Array.from(c.querySelectorAll(s));

  // ---------- Dark ----------
  const darkBtn = $('#dark');
  function setDark(on){ document.documentElement.classList.toggle('dark', !!on);
    try{ localStorage.setItem('chronik_dark', on ? '1':'0'); }catch(e){} }
  try{ setDark(localStorage.getItem('chronik_dark') === '1'); }catch(e){}
  if(darkBtn) darkBtn.onclick = () => setDark(!document.documentElement.classList.contains('dark'));

  // ---------- State ----------
  let activeGroup = null, activeLabel = null, activeFile = null;
  let aggQuery = '', detQuery = '';

  const fltAgg = $('#fltAgg'), fltDet = $('#fltDet');
  const clearDet = $('#clearDet'), clearGroups = $('#clearGroups'), clearLabels = $('#clearLabels');
  const aggCount = $('#aggCount'), detCount = $('#detCount');

  // ---------- Normalisierung + Fuzzy ≤1 (inkl. Transposition) ----------
  function norm(s){
    return String(s||"").toLowerCase()
      .normalize('NFD').replace(/[\u0300-\u036f]/g,'')
      .replace(/ä/g,'ae').replace(/ö/g,'oe').replace(/ü/g,'ue').replace(/ß/g,'ss')
      .replace(/[^\p{L}\p{N}]+/gu,' ')
      .replace(/[vu]/g,'v').replace(/[ij]/g,'i')
      .trim();
  }
  function edit1(a,b){
    if(a===b) return true;
    const la=a.length, lb=b.length;
    if(Math.abs(la-lb)>1) return false;
    let i=0,j=0,ed=0;
    while(i<la && j<lb){
      if(a[i]===b[j]){i++;j++;continue;}
      ed++; if(ed>1) return false;
      if(la>lb){ i++; } else if(lb>la){ j++; } else {
        if(a[i+1]===b[j] && a[i]===b[j+1]){ i+=2; j+=2; } else { i++; j++; }
      }
    }
    if(i<la || j<lb) ed++;
    return ed<=1;
  }
  function fuzzyLabelMatch(labelNorm, qNorm){
    if(!qNorm) return false;
    return labelNorm.includes(qNorm) || edit1(labelNorm, qNorm);
  }

  // ---------- Vorindexierung für Speed ----------
  const aggRows = $$('#agg tbody tr').map(row=>{
    const g=row.dataset.group||'', l=row.dataset.label||'';
    return {row, g, l, gn:norm(g), ln:norm(l), hay:norm(g+' '+l)};
  });
  const detRows = $$('#det tbody tr').map(row=>{
    const tds=row.children;
    const file=tds[0]?.innerText||'', page=tds[1]?.innerText||'';
    const g=row.dataset.group||'', l=row.dataset.label||'';
    const pat=tds[4]?.innerText||'', ctx=tds[5]?.innerText||'';
    return {
      row, g, l,
      f:file, fn:norm(file), pn:norm(page), gn:norm(g), ln:norm(l),
      pan:norm(pat), cxn:norm(ctx)
    };
  });

  // Gruppenfarben an vorhandene Label-Buttons (Tabellen)
  (function decorateByGroup(){
    const stats = (window.LABEL_STATS||{});
    $$('#agg .lbl, #det .lbl').forEach(b=>{
      const s=stats[b.dataset.label]; if(s&&s.group) b.dataset.group=String(s.group);
    });
  })();

  // ---------- Query ----------
  function parseQuery(q){
    const out = { group:null, label:null, file:null, pattern:null, text:[] };
    const toks=(q||'').trim().split(/\s+/).filter(Boolean);
    for(const t of toks){
      const m=t.match(/^(\w+):(.*)$/);
      if(m){ const k=m[1].toLowerCase(), v=norm(m[2]);
        if(k==='group') out.group=v; else if(k==='label') out.label=v;
        else if(k==='file') out.file=v; else if(k==='pattern') out.pattern=v;
        else out.text.push(norm(t));
      }else out.text.push(norm(t));
    }
    return out;
  }

  // ---------- Filterfunktionen ----------
  function visibleAgg(r, q){
    if(activeGroup && r.g!==activeGroup) return false;
    if(activeLabel && r.l!==activeLabel) return false;
    if(!q) return true;
    const pq=parseQuery(q);
    if(pq.group && !r.gn.includes(pq.group)) return false;
    if(pq.label){ if(!fuzzyLabelMatch(r.ln, pq.label)) return false; }
    for(const t of pq.text){ if(!r.hay.includes(t)) return false; }
    return true;
  }
  function visibleDet(r, q){
    if(activeGroup && r.g!==activeGroup) return false;
    if(activeLabel && r.l!==activeLabel) return false;
    if(activeFile && r.f!==activeFile) return false;
    if(!q) return true;
    const pq=parseQuery(q);
    if(pq.group && !r.gn.includes(pq.group)) return false;
    if(pq.label && !r.ln.includes(pq.label)) return false; // Details: nur includes
    if(pq.file  && !r.fn.includes(pq.file)) return false;
    if(pq.pattern && !(r.pan.includes(pq.pattern) || r.cxn.includes(pq.pattern))) return false;
    for(const t of pq.text){ if(!(r.fn.includes(t)||r.pan.includes(t)||r.cxn.includes(t)||r.ln.includes(t)||r.gn.includes(t))) return false; }
    return true;
  }

  // ---------- Batch-Anwendung ----------
  function applyAgg(){
    let vis=0;
    for(const r of aggRows){ const ok=visibleAgg(r, aggQuery); if(ok) vis++; r.row.classList.toggle('hidden', !ok); }
    if(aggCount) aggCount.textContent=String(vis);
  }
  function applyDet(){
    let vis=0;
    for(const r of detRows){ const ok=visibleDet(r, detQuery); if(ok) vis++; r.row.classList.toggle('hidden', !ok); }
    if(detCount) detCount.textContent=String(vis);
  }

  // ---------- Auswahl ----------
  function selectLabel(label){
    activeLabel = label;
    $$('.chip.label').forEach(c=>c.classList.toggle('active', c.dataset.label===activeLabel));
    applyAgg(); applyDet();
  }
  function selectFile(file){
    activeFile = file;
    $$('.chip.file').forEach(c=>c.classList.toggle('active', c.dataset.file===activeFile));
    applyDet();
  }

  // ---------- Bindings ----------
  // Debounce
  const debounce=(fn,ms)=>{ let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a),ms); }; };
  if(fltAgg){ fltAgg.oninput = debounce(()=>{ aggQuery = fltAgg.value||''; applyAgg(); }, 120); }
  if(fltDet){ fltDet.oninput = debounce(()=>{ detQuery = fltDet.value||''; applyDet(); }, 120); }
  if(clearDet){ clearDet.onclick = ()=>{ detQuery=''; if(fltDet) fltDet.value=''; applyDet(); }; }

  $$('.chip.group').forEach(ch=>{
    ch.addEventListener('click', ()=>{
      const g=ch.dataset.group; activeGroup=(activeGroup===g)?null:g;
      $$('.chip.group').forEach(c=>c.classList.toggle('active', c.dataset.group===activeGroup));
      applyAgg(); applyDet();
    });
  });
  if(clearGroups){ clearGroups.onclick = ()=>{ activeGroup=null; $$('.chip.group').forEach(c=>c.classList.remove('active')); applyAgg(); applyDet(); }; }

  // Datei-Chips
  $$('.chip.file').forEach(ch=> ch.addEventListener('click', ()=> selectFile(ch.dataset.file)));
  if(clearLabels){ clearLabels.onclick = ()=>{ selectLabel(null); selectFile(null); }; }

  // Label-Buttons in Tabellen
  $$('#agg .lbl, #det .lbl').forEach(b=> b.addEventListener('click', ()=> selectLabel(b.dataset.label)));

  // ---------- Init ----------
  applyAgg(); applyDet();
})();