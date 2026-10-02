let jid=null;
const $=id=>document.getElementById(id);
function show(id){$(id).classList.remove('hidden')}
function parsePages(s,total){
  const out=new Set();
  for(const part of s.split(',')){
    const x=part.trim(); if(!x) continue;
    if(x.includes('-')){
      let [a,b]=x.split('-').map(Number);
      if(Number.isFinite(a)&&Number.isFinite(b)){
        if(a>b)[a,b]=[b,a];
        for(let i=Math.max(1,a);i<=Math.min(total,b);i++)out.add(i);
      }
    }else{
      const n=Number(x); if(Number.isFinite(n)&&n>=1&&n<=total)out.add(n);
    }
  }
  return [...out].sort((a,b)=>a-b);
}
async function poll(){
  if(!jid)return;
  const r=await fetch(`/api/jobs/${jid}`); const s=await r.json();
  const pct=s.total?Math.round(s.current/s.total*100):0;
  $('bar').style.width=pct+'%';
  $('status').textContent=`${s.current} / ${s.total} — ${s.status}${s.error?' — '+s.error:''}`;
  if(s.status==='done'){show('download');$('download').href=`/api/jobs/${jid}/download`;show('review')}
  if(s.pages){
    let good=0,review=0,ai=0;
    for(const p of Object.values(s.pages)){
      if(p.label==='good')good++; else if(p.label==='review')review++; else ai++;
    }
    $('details').textContent=`Pagine elaborate: ${Object.keys(s.pages).length}\n🟢 buone: ${good}\n🟡 da revisionare: ${review}\n🔴 AI consigliata: ${ai}`;
  }
  if(s.status==='running'||s.status==='queued')setTimeout(poll,2500);
}
$('create').onclick=async()=>{
  const f=$('file').files[0]; if(!f){$('msg').textContent='Scegli prima un PDF.';return}
  $('msg').textContent='Caricamento...';
  const fd=new FormData();fd.append('file',f);
  const r=await fetch('/api/jobs',{method:'POST',body:fd}); const s=await r.json();
  jid=s.id; show('project'); $('msg').textContent=`Progetto creato: ${s.total} pagine`;
};
$('start').onclick=async()=>{await fetch(`/api/jobs/${jid}/start`,{method:'POST'});poll()};
$('pause').onclick=async()=>{await fetch(`/api/jobs/${jid}/pause`,{method:'POST'});poll()};
$('ai').onclick=async()=>{
  const r=await fetch(`/api/jobs/${jid}`); const s=await r.json();
  const pages=parsePages($('pages').value,s.total);
  if(!pages.length){alert('Inserisci almeno una pagina.');return}
  await fetch(`/api/jobs/${jid}/ai`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pages})});
  alert(`Selezionate ${pages.length} pagine. Per applicare il secondo passaggio, avvia il progetto.`);
};
