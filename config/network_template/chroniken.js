/**
 * Chroniken Netz – Besser:
 * - Mehr Farben (12+), dynamische Slot-Styles
 * - Multi-Select zeigt Schnittmenge gemeinsamer Nachbarn bzw. direkte Kanten bei gemischten Rollen
 * - Pfeile: Pan; Ctrl/Alt+Pfeile: zum nächsten sichtbaren Nachbarn in Richtung; Shift = schneller bzw. additiv
 * - Merkt letzte CSV: 1) File System Access (IndexedDB Handle)  2) Cache des letzten Payloads  3) Server-Autoload
 * Drop-in: dieses <script> ersetzt das alte.
 */

(function(){
  'use strict';

  // --------- State ---------
  let cy = null;
  let invertFocus = false, showLabels = true, minWeight = 1;
  let sticky = false, currentId = null;
  let filterActive = false;
  let keysBound = false;

  // Multi-Selection
  let selected = [];
  let isShift = false;

  // --------- Slot-Farben ---------
  const SLOT_COLORS = {
    1:'#2a62a6', 2:'#1b7f5e', 3:'#c0362c', 4:'#b7791f', 5:'#6d3d9a',
    6:'#0ea5e9', 7:'#10b981', 8:'#f59e0b', 9:'#ef4444', 10:'#a855f7', 11:'#f43f5e', 12:'#22d3ee'
  };
  const slots = Object.keys(SLOT_COLORS).map(n=>parseInt(n,10));

  // --------- Prefs ---------
  const PKEY = 'chroniken_web_prefs';
  const loadPrefs = () => { try{ return JSON.parse(localStorage.getItem(PKEY)||'{}'); } catch { return {}; } };
  const savePrefs = p => localStorage.setItem(PKEY, JSON.stringify(p||{}));
  const setPref = (k,v) => { const p = loadPrefs(); p[k]=v; savePrefs(p); };

  // --------- Minimal IndexedDB für FS-Handle ---------
  const DB_NAME='chroniken-db', STORE='kv';
  function idbOpen(){ return new Promise((res,rej)=>{ const r=indexedDB.open(DB_NAME,1); r.onupgradeneeded=()=>{ r.result.createObjectStore(STORE); }; r.onsuccess=()=>res(r.result); r.onerror=()=>rej(r.error); }); }
  async function idbSet(key,val){ const db=await idbOpen(); return new Promise((res,rej)=>{ const tx=db.transaction(STORE,'readwrite'); tx.objectStore(STORE).put(val,key); tx.oncomplete=()=>res(); tx.onerror=()=>rej(tx.error); }); }
  async function idbGet(key){ const db=await idbOpen(); return new Promise((res,rej)=>{ const tx=db.transaction(STORE,'readonly'); const req=tx.objectStore(STORE).get(key); req.onsuccess=()=>res(req.result); req.onerror=()=>rej(req.error); }); }

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
  if (typeof prefs.autoloadClient !== 'boolean') setPref('autoloadClient', true);

  // --------- Color helpers ---------
  const hexToRgb = h => { const m=h.replace('#',''); return {r:parseInt(m.slice(0,2),16), g:parseInt(m.slice(2,4),16), b:parseInt(m.slice(4,6),16)}; };
  const rgbToHex = (r,g,b) => { const c = n => Math.max(0,Math.min(255,Math.round(n))).toString(16).padStart(2,'0'); return `#${c(r)}${c(g)}${c(b)}`; };
  function screenBlendHex(list){
    if(!list.length) return null;
    let r=1, g=1, b=1;
    for(const h of list){ const {r:rr,g:gg,b:bb}=hexToRgb(h); r*=(1-rr/255); g*=(1-gg/255); b*=(1-bb/255); }
    r = 1-r; g = 1-g; b = 1-b;
    r = r + (1-r)*0.12; g = g + (1-g)*0.12; b = b + (1-b)*0.12;
    return rgbToHex(r*255,g*255,b*255);
  }

  // --------- Styles ---------
  function slotStyles(){
    const arr = [];
    for(const [k,col] of Object.entries(SLOT_COLORS)){
      arr.push({ selector:`.cy-pick${k}`, style:{ 'border-color':col,'border-width':3 }});
      arr.push({ selector:`.cy-sel${k}`,  style:{ 'line-color':col,'width':3 }});
    }
    return arr;
  }
  function baseStyle(){
    return [
      { selector:'core', style:{ 'selection-box-color':'#3b82f6','selection-box-opacity':0.15 }},
      { selector:'node', style:{
          'shape':'ellipse','width':'mapData(mass, 1, 15, 22, 66)','height':'mapData(mass, 1, 15, 22, 66)',
          'background-color': ele => ele.data('role')==='chronik' ? '#1d4f58' : '#c89b3c',
          'border-color': ele => ele.data('role')==='chronik' ? '#0a1a1f' : '#5a3f0b','border-width':1,
          'label': ele => { if(!showLabels) return ''; if(!filterActive) return ele.data('label')||''; return ele.data('isHL') ? (ele.data('label')||'') : ''; },
          'font-size': ele => ele.data('isHL') ? 16 : 14,
          'text-wrap':'wrap','text-max-width':160,'text-halign':'center','text-valign':'bottom','text-margin-y':10,
          'color':'#f4f1e9','text-background-color':'#0b0f14','text-background-opacity':0.78,'text-background-padding':3,
          'text-halo-color':'#0b0f14','text-halo-opacity':0.95,'text-halo-blur':1,'z-index-compare':'manual','z-index':0
      }},
      { selector:'node[role="work"]', style:{ 'shape':'diamond' }},
      { selector:'edge', style:{ 'line-color':'#6b7280','width':'mapData(weight, 1, 15, 1, 4)','curve-style':'bezier','opacity':0.98 }},
      { selector:'.dim', style:{ 'opacity':0.55 }},
      { selector:'.emph', style:{ 'border-width':3,'border-color':'#22c55e','z-index':1000,'opacity':1 }},
      { selector:'.edge-highlight', style:{ 'line-color':'#22c55e','width':3 }},
      { selector:'.hide', style:{ 'display':'none' }},
      { selector:'.hideFilter', style:{ 'display':'none' }},
      { selector:'.greyed', style:{ 'opacity':0.25 }},
      { selector:'.cy-multi', style:{ 'line-style':'dashed','width':4 } },
      { selector:'node[selCount > 0]', style:{ 'background-color':'data(mixColor)','border-color':'data(mixColor)','background-opacity':1 }},
      { selector:'node[selCount > 1]', style:{ 'border-width':4 }},
      ...slotStyles()
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
    console.debug('[chroniken] graph set: nodes', cy.nodes().length, 'edges', cy.edges().length);
  }
  function setLayout(name){
    if(!cy) return false;
    let opts = {name:'preset'};
    if(name==='fcose'){ opts = {name:'fcose', animate:false, quality:'default', randomize:false, nodeSeparation:96, idealEdgeLength:148, gravity:0.24}; }
    else if(name==='breadthfirst'){ opts = {name:'breadthfirst', directed:false, spacingFactor:1.85, wrap:true, roots: cy.nodes('[role="work"]')}; }
    else if(name==='grid'){ opts = {name:'grid', avoidOverlap:true, condense:true, spacingFactor:1.45}; }
    cy.layout(opts).run(); cy.fit(); return true;
  }

  // --------- Selection helpers ---------
  function clearSelection(){
    if(!cy) return;
    cy.startBatch();
    cy.nodes().forEach(n=>{
      slots.forEach(s=> n.removeClass(`cy-pick${s}`));
      n.data('selCount',0); n.data('isHL',0); n.removeData('mixColor'); n.removeClass('greyed');
    });
    cy.edges().forEach(e=>{
      slots.forEach(s=> e.removeClass(`cy-sel${s}`));
      e.removeClass('hideFilter'); e.removeClass('cy-multi');
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
  function addOrKeep(id){
    if(selected.some(s=>s.id===id)) return;
    addOrToggle(id);
  }
  function refreshEdgeColors(){
    if(!cy) return;
    cy.startBatch();
    cy.edges().forEach(e=>{
      slots.forEach(s=> e.removeClass(`cy-sel${s}`));
      e.removeClass('cy-multi');
    });
    const counts = new Map();
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
      const isDirectSel = selected.some(s => s.id === n.id());
      n.connectedEdges().forEach(e=>{
        if(!e.visible()) return; // berücksichtigt hide + hideFilter
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
        n.data('selCount', 0);
        if(isDirectSel){
          n.data('isHL', 1);
          const direct = selected.find(s=>s.id===n.id());
          n.data('mixColor', direct ? SLOT_COLORS[direct.slot] : null);
        } else {
          n.data('isHL', 0); n.removeData('mixColor');
        }
      }
    });
    cy.endBatch();
  }

  // --------- Schnittmengen-Filter ---------
  function applyHighlightFilter(){
    filterActive = selected.length > 0;
    cy.startBatch();

    if(!filterActive){
      cy.edges().removeClass('hideFilter');
      cy.nodes().removeClass('greyed');
      recomputeNodeMixAndHL();
      cy.endBatch(); cy.style().update(); return;
    }

    const selNodes = selected.map(s=>cy.$id(s.id)).filter(n=>n && n.length);
    const visibleEdgeIds = new Set();
    const visibleNodeIds = new Set(selNodes.map(n=>n.id()));

    if(selNodes.length === 1){
      selNodes[0].connectedEdges().forEach(e=>{
        if(e.visible()) { visibleEdgeIds.add(e.id()); visibleNodeIds.add(e.source().id()); visibleNodeIds.add(e.target().id()); }
      });
    } else {
      const roles = new Set(selNodes.map(n=>n.data('role')));
      if(roles.size === 1){
        let common = null;
        for(const n of selNodes){
          const neigh = n.neighborhood('node');
          const set = new Set(neigh.map(x=>x.id()));
          common = common ? new Set([...common].filter(id=>set.has(id))) : set;
        }
        if(common && common.size){
          for(const n of selNodes){
            for(const cid of common){
              const eCol = n.edgesWith(cy.$id(cid)).filter(e=>e.visible());
              eCol.forEach(e=>{ visibleEdgeIds.add(e.id()); visibleNodeIds.add(n.id()); visibleNodeIds.add(cid); });
            }
          }
        }
      } else {
        for(let i=0;i<selNodes.length;i++){
          for(let j=i+1;j<selNodes.length;j++){
            const eCol = selNodes[i].edgesWith(selNodes[j]).filter(e=>e.visible());
            eCol.forEach(e=>{ visibleEdgeIds.add(e.id()); visibleNodeIds.add(e.source().id()); visibleNodeIds.add(e.target().id()); });
          }
        }
      }
    }

    cy.edges().forEach(e=>{
      if(visibleEdgeIds.has(e.id())) e.removeClass('hideFilter'); else e.addClass('hideFilter');
    });

    recomputeNodeMixAndHL();
    cy.nodes().forEach(n=>{
      if(visibleNodeIds.has(n.id()) || n.data('isHL')) n.removeClass('greyed'); else n.addClass('greyed');
    });

    cy.endBatch();
    cy.style().update();
  }

  // --------- Suche ---------
  function selectByLabel(term, additive){
    if(!cy) return 0;
    const t = (term||'').trim().toLowerCase();
    if(!additive) clearSelection();
    if(!t) { applyHighlightFilter(); return 0; }
    let cnt = 0;
    cy.nodes().forEach(n=>{
      const label = (n.data('label')||'').toLowerCase();
      if(label.includes(t)){ addOrKeep(n.id()); cnt++; }
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

  // --------- Stats ---------
  function updateStats(){
    if(!cy){ elStats.textContent='—'; return; }
    elStats.textContent = `${cy.nodes().length} Knoten • ${cy.edges().length} Kanten • Auswahl: ${selected.length}`;
  }

  // --------- Navigation per Pfeilen ---------
  const PAN_STEP = 80, PAN_STEP_FAST = 180;
  function panBy(dx,dy,fast){
    if(!cy) return;
    const step = fast ? PAN_STEP_FAST : PAN_STEP;
    cy.panBy({x: dx*step, y: dy*step});
  }
  function getBaseNodeForNavigation(){
    if(!cy) return null;
    if(currentId){ const n=cy.$id(currentId); if(n && n.length) return n; }
    if(selected.length){ const n=cy.$id(selected[0].id); if(n && n.length) return n; }
    const bb = cy.extent(); const cx=(bb.x1+bb.x2)/2, cyy=(bb.y1+bb.y2)/2;
    let best=null, bestD=Infinity;
    cy.nodes(':visible').forEach(n=>{
      const p=n.position(); const dx=p.x-cx, dy=p.y-cyy; const d=dx*dx+dy*dy;
      if(d<bestD){ bestD=d; best=n; }
    });
    return best;
  }
  function moveSelection(dirX, dirY, additive){
    const base = getBaseNodeForNavigation(); if(!base) return;
    const cand = [];
    base.connectedEdges(':visible').forEach(e=>{
      const other = e.source().id()===base.id()? e.target(): e.source();
      if(other && other.length) cand.push(other[0]);
    });
    if(!cand.length) return;
    const bp = base.position();
    let best=null, bestScore=-Infinity;
    for(const n of cand){
      const p=n.position(); const vx=p.x-bp.x, vy=p.y-bp.y;
      const dot = vx*dirX + vy*dirY;
      if(dot<=0) continue;
      const len = Math.hypot(vx,vy) || 1;
      const score = dot/len;
      if(score>bestScore){ bestScore=score; best=n; }
    }
    if(!best) return;
    if(!additive) clearSelection();
    addOrKeep(best.id());
    refreshEdgeColors();
    applyHighlightFilter();
    applyFocus(best,true);
    updateStats();
    cy.fit(cy.collection(best), 80);
  }

  // --------- File System Access Helpers ---------
  const CAN_FS = !!window.showOpenFilePicker;
  async function ensureFilePermission(handle){
    if(!handle || !handle.queryPermission) return false;
    const q = await handle.queryPermission({mode:'read'});
    if(q==='granted') return true;
    if(handle.requestPermission){
      const r = await handle.requestPermission({mode:'read'});
      return r==='granted';
    }
    return false;
  }
  async function loadFromFSHandle(handle){
    if(!handle) return false;
    const ok = await ensureFilePermission(handle);
    if(!ok) return false;
    const file = await handle.getFile();
    const fd = new FormData(); fd.append('file', file);
    elFname.textContent = 'Lade aus Datei…';
    try{
      const res = await fetch('/api/upload_csv',{method:'POST', body:fd});
      const payload = await res.json();
      if(!res.ok) throw new Error(payload.error||'Upload fehlgeschlagen');
      setGraph(payload);
      elFname.textContent = `Geladen: ${file.name}`;
      cacheLastAfterLoad('upload-fs', file.name, file.size, null);
      await idbSet('lastCSVHandle', handle);
      console.debug('[chroniken] FS handle gespeichert');
      return true;
    }catch(err){
      console.debug('[chroniken] FS load error', err);
      elFname.textContent = `Fehler: ${err}`;
      return false;
    }
  }

  // --------- Events ---------
  function isTypingContext(){
    const a = document.activeElement;
    return a && (a.tagName==='INPUT' || a.tagName==='TEXTAREA' || a.isContentEditable);
  }
  function bindEvents(){
    if(!cy) return;

    cy.on('tap','node',(ev)=>{
      const n = ev.target;
      const additive = isShift || (ev.originalEvent && ev.originalEvent.shiftKey);
      if(!additive) clearSelection();
      addOrKeep(n.id());
      refreshEdgeColors();
      applyHighlightFilter();
      applyFocus(n,true);
      updateStats();
    });
    cy.on('tap',(ev)=>{ if(ev.target===cy){ clearFocus(); }});

    if(!keysBound){
      document.addEventListener('keydown',(e)=>{
        if(e.key==='Shift') isShift = true;

        const isArrow = e.key==='ArrowLeft' || e.key==='ArrowRight' || e.key==='ArrowUp' || e.key==='ArrowDown';
        if(isArrow && !isTypingContext()){
          e.preventDefault();
          const fast = e.shiftKey;
          if(e.ctrlKey || e.altKey){
            const dir = e.key==='ArrowLeft' ? [-1,0] :
                        e.key==='ArrowRight'? [1,0] :
                        e.key==='ArrowUp'   ? [0,-1] : [0,1];
            moveSelection(dir[0], dir[1], e.shiftKey);
          } else {
            const d = e.key==='ArrowLeft' ? [-1,0] :
                      e.key==='ArrowRight'? [1,0] :
                      e.key==='ArrowUp'   ? [0,-1] : [0,1];
            panBy(d[0], d[1], fast);
          }
          return;
        }

        if(e.key==='Escape'){ clearFocus(); clearSelection(); applyHighlightFilter(); }
        else if(e.key==='f'||e.key==='F'){ if(cy) cy.fit(); }
        else if(e.key==='+'||e.key==='='){ if(cy) cy.zoom(cy.zoom()*1.15); }
        else if(e.key==='-'){ if(cy) cy.zoom(cy.zoom()/1.15); }
      }, true);
      document.addEventListener('keyup',(e)=>{ if(e.key==='Shift') isShift = false; }, true);
      window.addEventListener('blur',()=>{ isShift=false; });
      keysBound = true;
    }
  }

  // --------- Wire UI ---------
  elPick.onclick = async () => {
    // Bevorzugt FS Picker, sonst klassischer <input type=file>
    if(CAN_FS){
      try{
        const [handle] = await window.showOpenFilePicker({types:[{description:'CSV', accept:{'text/csv':['.csv']}}]});
        await loadFromFSHandle(handle);
      }catch(err){ /* abgebrochen oder Fehler -> nichts tun */ }
    } else {
      elFile.click();
    }
  };
  elFile.onchange = () => { elFname.textContent = elFile.files[0] ? elFile.files[0].name : 'Keine Datei'; };

  function cacheLastAfterLoad(source, name, fileSizeOrNull, payload){
    try{
      const meta = {source, name, size:fileSizeOrNull||null, ts:Date.now()};
      setPref('lastCSVMeta', meta);
      if(source==='upload' && payload){
        const s = JSON.stringify(payload);
        if(s.length <= 4_500_000){ setPref('lastPayload', s); }
        else { const p=loadPrefs(); delete p.lastPayload; savePrefs(p); }
      }
      if(source!=='upload'){
        const p=loadPrefs(); delete p.lastPayload; savePrefs(p);
      }
    }catch{/* ignore */}
  }

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
      cacheLastAfterLoad('upload', elFile.files[0].name, elFile.files[0].size, payload);
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
      cacheLastAfterLoad('server', payload.filename||'Server-CSV', null, null);
    }catch(err){ elFname.textContent = `Fehler: ${err}`; }
  };

  elLayout.onchange = () => { setPref('layout', elLayout.value); setLayout(elLayout.value); };
  elThresh.oninput  = () => { elThreshLabel.textContent = `≥ ${elThresh.value}`; };
  elThresh.onchange = () => { minWeight = parseInt(elThresh.value); setPref('minW',minWeight); updateEdgeThreshold(); };
  elLabels.onchange = () => { showLabels = elLabels.checked; setPref('labels',showLabels); cy.style().fromJson(baseStyle()).update(); };
  elInvert.onchange = () => { invertFocus = elInvert.checked; setPref('invert',invertFocus); clearFocus(); };

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
    // 1) File System Access Handle aus IndexedDB
    try{
      const handle = await idbGet('lastCSVHandle');
      if(handle && CAN_FS){
        const ok = await loadFromFSHandle(handle);
        if(ok){ $("fname").textContent = `Auto: ${ (loadPrefs().lastCSVMeta||{}).name || 'CSV' } (Datei)`; return; }
      }
    }catch{/* ignore */}

    // 2) Cache-Payload
    try{
      const p = loadPrefs();
      if(p.autoloadClient!==false && p.lastPayload){
        const cached = JSON.parse(p.lastPayload);
        setGraph(cached);
        const name = (p.lastCSVMeta && p.lastCSVMeta.name) || 'Upload-CSV';
        $("fname").textContent = `Auto: ${name} (Cache)`;
        return;
      }
    }catch{/* ignore */}

    // 3) Server-Autoload
    try{
      const res = await fetch('/api/autoload');
      if(res.ok){
        const payload = await res.json(); setGraph(payload);
        $("fname").textContent = `Auto: ${payload.filename||'Server-CSV'}`;
        cacheLastAfterLoad('server', payload.filename||'Server-CSV', null, null);
      }
    }catch{/* ignore */}
  })();

})();
