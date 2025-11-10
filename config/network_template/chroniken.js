(function(){
  'use strict';

  // --------- State ---------
  let cy = null;
  let invertFocus = false, showLabels = true, minWeight = 1;
  let sticky = false, currentId = null;
  let filterActive = false;        // true sobald Auswahl existiert

  // Multi-Selection
  let selected = [];               // [{id, slot}]
  const slots = [1,2,3,4,5];       // Farb-Slots
  let isShift = false;             // globaler Shift-Status

  // Slot-Farben (Ränder + Kanten)
  const SLOT_COLORS = {
    1:'#2a62a6', // blau
    2:'#1b7f5e', // grün
    3:'#c0362c', // rot
    4:'#b7791f', // ocker
    5:'#6d3d9a'  // purpur
  };

  // --------- Prefs ---------
  const PKEY = 'chroniken_web_prefs';
  const loadPrefs = () => { try{ return JSON.parse(localStorage.getItem(PKEY)||'{}'); } catch { return {}; } };
  const savePrefs = p => localStorage.setItem(PKEY, JSON.stringify(p||{}));
  const setPref = (k,v) => { const p = loadPrefs(); p[k]=v; savePrefs(p); };

  // --------- DOM ---------
  const $ = id => document.getElementById(id);
  const elFile = $('file'), elPick = $('pick'), elUpload = $('upload'), elAutoload = $('autoload'), elFname = $('fname');
  const elLayout = $('layout'), elThresh = $('thresh'), elThreshLabel = $('threshLabel');
  const elLabels = $('labels'), elInvert = $('invert');
  const elSearch = $('search'), elSearchBtn = $('searchBtn'), elClearSel = $('clearSel');
  const elExport = $('export'), elFit = $('fit'), elStats = $('stats');

  const prefs = loadPrefs();
  if (prefs.layout) elLayout.value = prefs.layout;
  if (typeof prefs.minW === 'number') { elThresh.value = String(prefs.minW); elThreshLabel.textContent = `≥ ${prefs.minW}`; minWeight = prefs.minW; }
  if (typeof prefs.labels === 'boolean') { elLabels.checked = prefs.labels; showLabels = prefs.labels; }
  if (typeof prefs.invert === 'boolean') { elInvert.checked = prefs.invert; invertFocus = prefs.invert; }

  // --------- Color helpers ---------
  const hexToRgb = h => { const m=h.replace('#',''); return {r:parseInt(m.slice(0,2),16), g:parseInt(m.slice(2,4),16), b:parseInt(m.slice(4,6),16)}; };
  const rgbToHex = (r,g,b) => {
    const c = n => Math.max(0,Math.min(255,Math.round(n))).toString(16).padStart(2,'0');
    return `#${c(r)}${c(g)}${c(b)}`;
  };
  // Screen-Blending: ergibt intuitive Mischungen (blau+rot→violett, +grün→hellere Mischung)
  function screenBlendHex(list){
    if(!list.length) return null;
    let r=1, g=1, b=1;
    for(const h of list){
      const {r:rr,g:gg,b:bb} = hexToRgb(h);
      r *= (1 - rr/255); g *= (1 - gg/255); b *= (1 - bb/255);
    }
    r = (1 - r); g = (1 - g); b = (1 - b);
    r = r + (1-r)*0.12; g = g + (1-g)*0.12; b = b + (1-b)*0.12;
    return rgbToHex(r*255,g*255,b*255);
  }

  // --------- Cytoscape Styles ---------
  function baseStyle(){
    return [
      { selector:'core', style:{ 'selection-box-color':'#3b82f6','selection-box-opacity':0.15 }},

      { selector:'node', style:{
          'shape':'ellipse',
          'width':'mapData(mass, 1, 15, 22, 66)',
          'height':'mapData(mass, 1, 15, 22, 66)',
          'background-color': ele => ele.data('role')==='chronik' ? '#1d4f58' : '#c89b3c',
          'border-color': ele => ele.data('role')==='chronik' ? '#0a1a1f' : '#5a3f0b',
          'border-width':1,
          'label': ele => {
            if(!showLabels) return '';
            if(!filterActive) return ele.data('label')||'';
            return ele.data('isHL') ? (ele.data('label')||'') : '';
          },
          'font-size': ele => ele.data('isHL') ? 16 : 14,
          'text-wrap':'wrap',
          'text-max-width': 160,
          'text-halign':'center',
          'text-valign':'bottom',
          'text-margin-y': 10,
          'color':'#f4f1e9',
          'text-background-color':'#0b0f14','text-background-opacity':0.78,'text-background-padding':3,
          'text-halo-color':'#0b0f14','text-halo-opacity':0.95,'text-halo-blur':1,
          'z-index-compare':'manual','z-index':0
      }},
      { selector:'node[role="work"]', style:{ 'shape':'diamond' }},

      { selector:'edge', style:{ 'line-color':'#6b7280','width':'mapData(weight, 1, 15, 1, 4)','curve-style':'bezier','opacity':0.98 }},

      // Fokus
      { selector:'.dim', style:{ 'opacity':0.55 }},
      { selector:'.emph', style:{ 'border-width':3,'border-color':'#22c55e','z-index':1000,'opacity':1 }},
      { selector:'.edge-highlight', style:{ 'line-color':'#22c55e','width':3 }},

      // Hiding-Klassen getrennt: Threshold vs. Filter
      { selector:'.hide', style:{ 'display':'none' }},
      { selector:'.hideFilter', style:{ 'display':'none' }},

      // Greyed Nodes wenn Filter aktiv
      { selector:'.greyed', style:{
        'opacity':0.25
      }},

      // Node-Auswahl-Ränder (direkt selektierte Knoten)
      { selector:'.cy-pick1', style:{ 'border-color':'#2a62a6','border-width':3 } },
      { selector:'.cy-pick2', style:{ 'border-color':'#1b7f5e','border-width':3 } },
      { selector:'.cy-pick3', style:{ 'border-color':'#c0362c','border-width':3 } },
      { selector:'.cy-pick4', style:{ 'border-color':'#b7791f','border-width':3 } },
      { selector:'.cy-pick5', style:{ 'border-color':'#6d3d9a','border-width':3 } },

      // Kantenfarben je Slot + Mehrfach
      { selector:'.cy-sel1', style:{ 'line-color':'#2a62a6','width':3 } },
      { selector:'.cy-sel2', style:{ 'line-color':'#1b7f5e','width':3 } },
      { selector:'.cy-sel3', style:{ 'line-color':'#c0362c','width':3 } },
      { selector:'.cy-sel4', style:{ 'line-color':'#b7791f','width':3 } },
      { selector:'.cy-sel5', style:{ 'line-color':'#6d3d9a','width':3 } },
      { selector:'.cy-multi', style:{ 'line-style':'dashed','width':4 } },

      // Knoteneinfärbung gemäß Mischung sichtbarer selektierter Kanten
      { selector:'node[selCount > 0]', style:{
        'background-color':'data(mixColor)',
        'border-color':'data(mixColor)',
        'background-opacity':1
      }},
      { selector:'node[selCount > 1]', style:{ 'border-width':4 }}
    ];
  }

  // --------- Focus ---------
  function clearFocus(){
    if(!cy) return;
    cy.nodes().removeClass('emph dim');
    cy.edges().removeClass('edge-highlight');
    currentId = null; sticky = false;
  }

  function applyFocus(node, makeSticky){
    if(!cy || !node || node.length===0) return;
    cy.nodes().removeClass('emph dim'); cy.edges().removeClass('edge-highlight');

    if(selected.length>1){ currentId=node.id(); sticky=!!makeSticky; return; }

    const role = node.data('role'); node.addClass('emph');
    if(invertFocus){
      const opp = role==='chronik' ? 'work' : 'chronik';
      const neighbors = node.neighborhood('node');
      const neighborSet = new Set(neighbors.filter(`[role="${opp}"]`).map(n=>n.id()));
      const oppNodes = cy.nodes(`[role="${opp}"]`);
      const nonNeighbors = oppNodes.filter(n=>!neighborSet.has(n.id()));
      nonNeighbors.addClass('emph'); cy.nodes().not(node).not(nonNeighbors).addClass('dim');
    } else {
      const neighNodes = node.closedNeighborhood('node');
      const neighEdges = node.connectedEdges();
      neighNodes.addClass('emph'); neighEdges.addClass('edge-highlight');
      cy.nodes().difference(neighNodes).addClass('dim');
    }
    currentId = node.id(); sticky = !!makeSticky;
  }

  // --------- Threshold/Labels ---------
  function updateEdgeThreshold(){
    if(!cy) return;
    cy.startBatch();
    cy.edges().forEach(e=>{
      const w = e.data('weight') || 1;
      if(w<minWeight) e.addClass('hide'); else e.removeClass('hide');
    });
    cy.endBatch();
    // Filter neu anwenden, da sich sichtbare Kanten geändert haben
    applyHighlightFilter();
  }
  function toggleLabels(show){ showLabels = !!show; cy.style().fromJson(baseStyle()).update(); }

  // --------- Graph ---------
  function setGraph(payload){
    const elements = (payload&&payload.nodes) ? payload.nodes.concat(payload.edges) : [];
    if(cy){ cy.destroy(); cy = null; }
    const pxr = Math.max(2, Math.floor(window.devicePixelRatio||1));
    cy = cytoscape({
      container: document.getElementById('cy'),
      elements, style: baseStyle(), layout:{name:'preset'},
      wheelSensitivity:0.22, motionBlur:false, textureOnViewport:true, pixelRatio:pxr
    });
    cy.nodes().forEach(n=>{ n.data('selCount',0); n.data('isHL',0); n.removeData('mixColor'); });
    bindEvents();
    toggleLabels(showLabels);
    updateEdgeThreshold();
    const lay = (loadPrefs().layout||'preset'); if(lay!=='preset') setLayout(lay); else cy.fit();
    updateStats();
  }

  function setLayout(name){
    if(!cy) return false;
    let opts = {name:'preset'};
    if(name==='fcose'){ opts = {name:'fcose', animate:false, quality:'default', randomize:false, nodeSeparation:96, idealEdgeLength:148, gravity:0.24}; }
    else if(name==='breadthfirst'){ opts = {name:'breadthfirst', directed:false, spacingFactor:1.85, wrap:true, roots: cy.nodes('[role="work"]')}; }
    else if(name==='grid'){ opts = {name:'grid', avoidOverlap:true, condense:true, spacingFactor:1.45}; }
    cy.layout(opts).run(); cy.fit(); return true;
  }

  // --------- Selection ---------
  function clearSelection(){
    if(!cy) return;
    cy.startBatch();
    cy.nodes().forEach(n=>{
      slots.forEach(s=> n.removeClass(`cy-pick${s}`));
      n.data('selCount',0); n.data('isHL',0); n.removeData('mixColor'); n.removeClass('greyed');
    });
    cy.edges().forEach(e=>{
      slots.forEach(s=> e.removeClass(`cy-sel${s}`));
      e.removeClass('cy-multi'); e.removeClass('hideFilter');
    });
    cy.endBatch();
    selected = [];
    filterActive = false;
    cy.style().update();
    updateStats();
  }

  function addOrToggle(id){
    const idx = selected.findIndex(x=>x.id===id);
    if(idx>=0){
      const slot = selected[idx].slot;
      selected.splice(idx,1);
      const n = cy.$id(id);
      if(n && n.length) n.removeClass(`cy-pick${slot}`);
      return;
    }
    const used = new Set(selected.map(x=>x.slot));
    const slot = slots.find(s=>!used.has(s)) || slots[0];
    selected.push({id, slot});
    const n = cy.$id(id);
    if(n && n.length) n.addClass(`cy-pick${slot}`);
  }

  function refreshEdgeColors(){
    if(!cy) return;
    cy.startBatch();
    // Reset classes
    cy.edges().forEach(e=>{
      slots.forEach(s=> e.removeClass(`cy-sel${s}`));
      e.removeClass('cy-multi');
    });
    // Apply per selection
    const counts = new Map(); // edgeId -> number of selections touching it
    selected.forEach(sel=>{
      const n = cy.$id(sel.id);
      if(!n || !n.length) return;
      n.connectedEdges().forEach(e=>{
        e.addClass(`cy-sel${sel.slot}`);
        counts.set(e.id(), (counts.get(e.id())||0)+1);
      });
    });
    counts.forEach((c,eid)=>{ if(c>1) cy.$id(eid).addClass('cy-multi'); });
    cy.endBatch();
  }

  function recomputeNodeMixAndHL(){
    if(!cy) return;
    cy.startBatch();
    cy.nodes().forEach(n=>{
      const slotSet = new Set();
      // direkte Selektion?
      const isDirectSel = selected.some(s => s.id === n.id());
      n.connectedEdges().forEach(e=>{
        if(e.hasClass('hide')) return; // unsichtbare Kanten durch Schwelle ignorieren
        for(const s of slots){ if(e.hasClass(`cy-sel${s}`)) slotSet.add(s); }
      });
      const arr = [...slotSet];
      if(arr.length>0){
        const cols = arr.map(s=>SLOT_COLORS[s]).filter(Boolean);
        const mix  = screenBlendHex(cols);
        n.data('selCount', arr.length);
        n.data('mixColor', mix);
        n.data('isHL', 1);
      } else {
        // Wenn direkt selektiert, trotzdem als Highlight markieren
        n.data('selCount', 0);
        if(isDirectSel){ n.data('isHL', 1); n.data('mixColor', SLOT_COLORS[(selected.find(s=>s.id===n.id())||{}).slot] || null); }
        else { n.data('isHL', 0); n.removeData('mixColor'); }
      }
    });
    cy.endBatch();
  }

  function applyHighlightFilter(){
    filterActive = selected.length > 0;
    // Edges: wenn Filter aktiv → nur selektierte sichtbar (zusätzlich zu Threshold)
    cy.startBatch();
    if(filterActive){
      cy.edges().forEach(e=>{
        const isSel = slots.some(s=> e.hasClass(`cy-sel${s}`));
        if(isSel) e.removeClass('hideFilter'); else e.addClass('hideFilter');
      });
    } else {
      cy.edges().removeClass('hideFilter');
    }
    // Nodes: wenn Filter aktiv → nur Highlight-Nodes normal, Rest greyed
    recomputeNodeMixAndHL();
    if(filterActive){
      cy.nodes().forEach(n=>{
        if(n.data('isHL')) n.removeClass('greyed'); else n.addClass('greyed');
      });
    } else {
      cy.nodes().removeClass('greyed');
    }
    cy.endBatch();
    cy.style().update();
  }

  function selectByLabel(term, additive){
    if(!cy) return 0;
    const t = (term||'').trim().toLowerCase();
    if(!additive) clearSelection();
    if(!t) { applyHighlightFilter(); return 0; }
    let cnt = 0;
    cy.nodes().forEach(n=>{
      const label = (n.data('label')||'').toLowerCase();
      if(label.includes(t)){ addOrToggle(n.id()); cnt++; }
    });
    refreshEdgeColors();
    applyHighlightFilter();
    if(selected.length){
      let eles = cy.collection();
      selected.forEach(s=>{ const c=cy.$id(s.id); if(c && c.length) eles = eles.union(c); });
      cy.fit(eles,50);
    }
    updateStats();
    return cnt;
  }

  function updateStats(){
    if(!cy){ elStats.textContent='—'; return; }
    elStats.textContent = `${cy.nodes().length} Knoten • ${cy.edges().length} Kanten • Auswahl: ${selected.length}`;
  }

  // --------- Events ---------
  function bindEvents(){
    if(!cy) return;

    cy.on('tap','node',(ev)=>{
      const n = ev.target;
      const additive = isShift || (ev.originalEvent && ev.originalEvent.shiftKey);
      if(!additive) clearSelection();
      addOrToggle(n.id());
      refreshEdgeColors();
      applyHighlightFilter();
      applyFocus(n,true);
      updateStats();
    });

    cy.on('tap',(ev)=>{ if(ev.target===cy){ clearFocus(); }});

    document.addEventListener('keydown',(e)=>{
      if(e.key==='Shift') isShift = true;
      else if(e.key==='Escape'){ clearFocus(); clearSelection(); applyHighlightFilter(); }
      else if(e.key==='f'||e.key==='F'){ if(cy) cy.fit(); }
      else if(e.key==='+'||e.key==='='){ if(cy) cy.zoom(cy.zoom()*1.15); }
      else if(e.key==='-'){ if(cy) cy.zoom(cy.zoom()/1.15); }
    }, true);
    document.addEventListener('keyup',(e)=>{ if(e.key==='Shift') isShift = false; }, true);
    window.addEventListener('blur',()=>{ isShift=false; });
  }

  // --------- Wire UI ---------
  elPick.onclick = () => elFile.click();
  elFile.onchange = () => { elFname.textContent = elFile.files[0] ? elFile.files[0].name : 'Keine Datei'; };

  elUpload.onclick = async () => {
    if(!elFile.files[0]){ elFname.textContent = 'Bitte CSV wählen'; return; }
    const fd = new FormData(); fd.append('file', elFile.files[0]);
    elFname.textContent = 'Lade…';
    try{
      const res = await fetch('/api/upload_csv',{method:'POST', body:fd});
      const payload = await res.json();
      if(!res.ok) throw new Error(payload.error||'Upload fehlgeschlagen');
      setGraph(payload);
      elFname.textContent = `Geladen: ${elFile.files[0].name}`;
    }catch(err){ elFname.textContent = `Fehler: ${err}`; }
  };

  elAutoload.onclick = async () => {
    elFname.textContent = 'Auto-Laden…';
    try{
      const res = await fetch('/api/autoload');
      const payload = await res.json();
      if(!res.ok) throw new Error(payload.error||'Kein Auto-Load gefunden');
      setGraph(payload);
      elFname.textContent = `Auto: ${payload.filename||'Server-CSV'}`;
    }catch(err){ elFname.textContent = `Fehler: ${err}`; }
  };

  elLayout.onchange = () => { setPref('layout', elLayout.value); setLayout(elLayout.value); };
  elThresh.oninput  = () => { elThreshLabel.textContent = `≥ ${elThresh.value}`; };
  elThresh.onchange = () => { minWeight = parseInt(elThresh.value); setPref('minW',minWeight); updateEdgeThreshold(); };
  elLabels.onchange = () => { showLabels = elLabels.checked; setPref('labels',showLabels); cy.style().fromJson(baseStyle()).update(); };
  elInvert.onchange = () => { invertFocus = elInvert.checked; setPref('invert',invertFocus); clearFocus(); };

  // Suche: Enter/Btn mit Shift = additiv
  elSearch.addEventListener('keydown',(e)=>{
    if(e.key==='Enter'){
      e.preventDefault();
      const additive = e.shiftKey || isShift;
      selectByLabel(elSearch.value, additive);
    }
  });
  elSearchBtn.onclick = e => {
    const additive = (e && 'shiftKey' in e && e.shiftKey) || isShift;
    selectByLabel(elSearch.value, additive);
  };
  elClearSel.onclick = () => { clearSelection(); applyHighlightFilter(); };

  elExport.onclick = ()=>{
    if(!cy) return;
    const dataUrl = cy.png({full:true, scale:3, bg:'#0b0f14'});
    const a = document.createElement('a'); a.href = dataUrl; a.download = 'chroniken_net.png';
    document.body.appendChild(a); a.click(); a.remove();
  };
  elFit.onclick = () => { if(cy) cy.fit(); };

  // --------- Initial Auto-Load ---------
  (async function tryAuto(){
    try{
      const res = await fetch('/api/autoload');
      if(res.ok){ const payload = await res.json(); setGraph(payload); $("fname").textContent = `Auto: ${payload.filename||'Server-CSV'}`; }
    }catch{/* ignore */}
  })();

})();