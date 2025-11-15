// ===== FlaskScope Report JS =====
(function(){
"use strict";
const $ = (q, r=document)=> r.querySelector(q);
const $$ = (q, r=document)=> Array.from(r.querySelectorAll(q));

function activateTabs(){
  const tabs = $$(".report-tab");
  const panes = $$(".report-pane");
  tabs.forEach(t=>{
    t.addEventListener("click", ()=>{
      tabs.forEach(x=>x.classList.remove("active"));
      t.classList.add("active");
      const tgt = t.dataset.target;
      panes.forEach(p=> p.style.display = (p.id===tgt) ? "block" : "none");
    });
  });
  if (tabs.length) tabs[0].click();
}
function activateSubTabs(rootId){
  const root = $("#"+rootId);
  if(!root) return;
  const tabs = root.querySelectorAll(".report-subtab");
  const panes= root.querySelectorAll(".report-subpane");
  tabs.forEach(t=>{
    t.addEventListener("click", ()=>{
      tabs.forEach(x=>x.classList.remove("active"));
      t.classList.add("active");
      const tgt=t.dataset.target;
      panes.forEach(p=> p.style.display = (p.id===tgt) ? "block" : "none");
    });
  });
  if(tabs.length) tabs[0].click();
}
function attachFilter(inpSel, listScopeSel){
  const inp = $(inpSel);
  if(!inp) return;
  inp.addEventListener("input", ()=>{
    const q = inp.value.trim().toLowerCase();
    $$(listScopeSel+" li").forEach(li=>{
      const ok = !q || li.textContent.toLowerCase().includes(q);
      li.style.display = ok ? "" : "none";
    });
  });
}

// -------- Graph (Canvas Force) --------
function runGraph(){
  const data = window.FS_DATA || {nodes:[], edges:[]};
  const cvs = $("#graph");
  if(!cvs) return;
  const ctx = cvs.getContext("2d");
  const filter = $("#graph-filter");
  const cbUsed = $("#graph-usedonly");

  function resize(){ cvs.width = cvs.clientWidth; cvs.height = cvs.clientHeight; }
  new ResizeObserver(resize).observe(cvs); resize();

  // Deep copy to avoid mutation issues
  const nodes = (data.nodes||[]).map(n=>({id:n.id,label:n.label,type:n.type,path:n.path,used:!!n.used,x:Math.random()*cvs.width,y:Math.random()*cvs.height,vx:0,vy:0}));
  const id2 = Object.fromEntries(nodes.map(n=>[n.id,n]));
  const edges = (data.edges||[]).map(e=>({a:id2[e.from], b:id2[e.to], t:e.type}));
  const K = 90, REP = 4200, DAMP = 0.86;

  function tick(){
    // Hooke (Federn)
    edges.forEach(e=>{
      if(!e.a || !e.b) return;
      const dx=e.b.x-e.a.x, dy=e.b.y-e.a.y;
      const dist=Math.max(1,Math.hypot(dx,dy));
      const k=(dist-K)*0.0026;
      const nx=dx/dist, ny=dy/dist;
      e.a.vx+=k*nx; e.a.vy+=k*ny; e.b.vx-=k*nx; e.b.vy-=k*ny;
    });
    // Coulomb (Abstoßung)
    for(let i=0;i<nodes.length;i++){
      for(let j=i+1;j<nodes.length;j++){
        const a=nodes[i], b=nodes[j];
        const dx=b.x-a.x, dy=b.y-a.y, d2=dx*dx+dy*dy+0.1;
        const f=REP/d2, dist=Math.sqrt(d2), nx=dx/dist, ny=dy/dist;
        a.vx-=f*nx; a.vy-=f*ny; b.vx+=f*nx; b.vy+=f*ny;
      }
    }
    nodes.forEach(n=>{
      n.vx*=DAMP; n.vy*=DAMP; n.x+=n.vx; n.y+=n.vy;
      n.x=Math.max(16,Math.min(cvs.width-16,n.x));
      n.y=Math.max(16,Math.min(cvs.height-16,n.y));
    });
  }

  function draw(){
    const q = (filter?.value||"").toLowerCase();
    const onlyUsed = cbUsed?.checked;
    ctx.clearRect(0,0,cvs.width,cvs.height);

    const showNode = (n)=>{
      if(onlyUsed && !n.used) return false;
      if(!q) return true;
      return (n.label?.toLowerCase().includes(q)) || (n.path?.toLowerCase().includes(q));
    };
    const visible = new Set(nodes.filter(showNode).map(n=>n.id));

    // Kanten
    ctx.globalAlpha=.22;
    ctx.lineWidth = 1;
    edges.forEach(e=>{
      if(!e.a||!e.b) return;
      if(!visible.has(e.a.id) || !visible.has(e.b.id)) return;
      ctx.beginPath(); ctx.moveTo(e.a.x,e.a.y); ctx.lineTo(e.b.x,e.b.y);
      ctx.strokeStyle="#94a3b8"; ctx.stroke();
    });

    // Knoten
    ctx.globalAlpha=1;
    nodes.forEach(n=>{
      if(!visible.has(n.id)) return;
      ctx.beginPath(); ctx.arc(n.x,n.y,5.5,0,Math.PI*2);
      ctx.fillStyle = n.type==="python"?"#2563eb":n.type==="template"?"#a16207":n.type==="js"?"#16a34a":n.type==="css"?"#6d28d9":"#64748b";
      ctx.fill();
    });
  }

  let step=0, settle=Math.min(300, 30 + nodes.length*2);
  function loop(){
    if(step<settle){ tick(); step++; }
    draw();
    requestAnimationFrame(loop);
  }
  loop();

  // Redraw on inputs
  filter?.addEventListener("input", draw);
  cbUsed?.addEventListener("change", draw);
}

// ---- init ----
document.addEventListener("DOMContentLoaded", ()=>{
  activateTabs();
  activateSubTabs("pane-conflicts");
  attachFilter("#filter-used",".list-used");
  attachFilter("#filter-unused",".list-unused");
  attachFilter("#filter-unusedfns",".list-unusedfns");
  runGraph();
});
})();