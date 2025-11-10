(function(){
  "use strict";

  function textOf(cell){return (cell.textContent||"").trim()}
  function isNumberHeader(th){return (th.getAttribute("data-sort")||"") === "num"}
  function compare(a,b,isNum,asc){
    if(isNum){
      const na=Number(a.replace(/\s+/g,"")); const nb=Number(b.replace(/\s+/g,""));
      return asc ? na-nb : nb-na;
    }
    return asc ? a.localeCompare(b, "de", {sensitivity:"base"}) : b.localeCompare(a, "de", {sensitivity:"base"});
  }

  function makeSortable(table){
    const thead=table.tHead; if(!thead) return;
    const headers=[...thead.rows[0].cells];
    headers.forEach((th,idx)=>{
      th.addEventListener("click",()=>{
        const tbody=table.tBodies[0]; if(!tbody) return;
        const asc = !(th.dataset.sortDir==="asc");
        headers.forEach(h=>h.removeAttribute("data-sort-dir"));
        th.dataset.sortDir = asc ? "asc" : "desc";
        const rows=[...tbody.rows];
        const isNum=isNumberHeader(th);
        rows.sort((r1,r2)=>compare(
          textOf(r1.cells[idx]),
          textOf(r2.cells[idx]),
          isNum,
          asc
        ));
        const frag=document.createDocumentFragment();
        rows.forEach(r=>frag.appendChild(r));
        tbody.innerHTML="";
        tbody.appendChild(frag);
      });
    });
  }

  function setupFilter(){
    const input=document.getElementById("authors_report-filter");
    const onlyText=document.getElementById("authors_report-only-text");
    const onlyBib=document.getElementById("authors_report-only-bib");
    const reset=document.getElementById("authors_report-reset");
    const table=document.getElementById("authors_report-edges");
    if(!input || !table) return;

    function apply(){
      const q=(input.value||"").toLowerCase();
      const wantText=!!onlyText?.checked;
      const wantBib=!!onlyBib?.checked;
      const rows=[...table.tBodies[0].rows];
      for(const tr of rows){
        const src=textOf(tr.cells[0]).toLowerCase();
        const tgt=textOf(tr.cells[1]).toLowerCase();
        const total=Number(textOf(tr.cells[2]));
        const bib=Number(textOf(tr.cells[3]));
        const txt=Number(textOf(tr.cells[4]));
        const ex=textOf(tr.cells[5]).toLowerCase();
        let show=true;
        if(q){
          const hay=src+" "+tgt+" "+ex;
          show = hay.indexOf(q) !== -1;
        }
        if(wantText) show = show && txt>0;
        if(wantBib) show = show && bib>0;
        tr.style.display = show ? "" : "none";
      }
    }

    input.addEventListener("input",apply);
    onlyText?.addEventListener("change",apply);
    onlyBib?.addEventListener("change",apply);
    reset?.addEventListener("click",()=>{
      input.value=""; if(onlyText) onlyText.checked=false; if(onlyBib) onlyBib.checked=false; apply();
    });
  }

  function setupTopButton(){
    const btn=document.getElementById("authors_report-top");
    if(!btn) return;
    window.addEventListener("scroll",()=>{
      if(window.scrollY>200) btn.classList.add("show"); else btn.classList.remove("show");
    });
    btn.addEventListener("click",()=>window.scrollTo({top:0,behavior:"smooth"}));
  }

  document.addEventListener("DOMContentLoaded", ()=>{
    document.querySelectorAll(".authors_report-sortable").forEach(makeSortable);
    setupFilter();
    setupTopButton();
  });
})();