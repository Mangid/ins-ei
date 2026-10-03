loginForm.onsubmit=async e=>{e.preventDefault();loginError.textContent="";const r=await fetch("/dev-portal/api/login",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({username:loginUser.value,password:loginPassword.value})});if(!r.ok){loginError.textContent="Anmeldung fehlgeschlagen.";return}await load()};
const pages=[...document.querySelectorAll(".page")];document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{document.querySelectorAll("nav button").forEach(x=>x.classList.remove("active"));pages.forEach(x=>x.classList.remove("active"));b.classList.add("active");document.getElementById(b.dataset.page).classList.add("active")});
const metric=(name,value)=>'<div class="card metric"><small>'+name+'</small><strong>'+value+'</strong></div>';
async function load(){try{const r=await fetch("/dev-portal/api/me",{cache:"no-store"});if(!r.ok)throw Error("HTTP "+r.status);const d=await r.json();loginView.classList.add("hidden");portalView.classList.remove("hidden");user.textContent=d.display_name||"Kunde";adminNav.classList.toggle("hidden",!d.is_admin);dashboardConfigNav?.classList.toggle("hidden",!d.is_admin);if(d.is_admin)loadUsers();loadOekofenPortal();if(!d.is_admin){document.querySelectorAll("nav button").forEach(x=>x.classList.remove("active"));pages.forEach(x=>x.classList.remove("active"));document.querySelector('nav button[data-page="systems"]')?.classList.add("active");document.getElementById("systems")?.classList.add("active")}summary.innerHTML=metric("Anlagen",d.systems.length)+metric("Status",d.status||"–");status.textContent=d.message||"Portal bereit."}catch(e){portalView.classList.add("hidden");loginView.classList.remove("hidden");}}load();
async function loadUsers(){const r=await fetch("/dev-portal/api/admin/users",{cache:"no-store"});if(!r.ok)return;const d=await r.json();window.portalDashboards=d.dashboards;newDashboards.innerHTML=d.dashboards.map(x=>'<label><input type="checkbox" value="'+x.id+'"> '+x.name+'</label>').join(" ");userList.innerHTML=d.users.map(x=>'<div class="user-row"><div><strong>'+x.display_name+'</strong><small>'+x.username+' · '+(x.role==="admin"?"Admin":x.role==="partner"?"Partner":"Endkunde")+(x.role==="end_customer"?" · "+(x.access_status==="trial"?"Test bis "+(x.trial_end||"–"):x.access_status==="suspended"?"Gesperrt":"Aktiv"):"")+(x.active?"":" · deaktiviert")+'</small></div><div>'+x.dashboard_ids.map(id=>'<span class="tag">'+id+'</span>').join("")+'</div><button onclick="editUser('+x.id+')">Bearbeiten</button></div>').join("")}
userCreate.onsubmit=async e=>{e.preventDefault();const dashboard_ids=[...newDashboards.querySelectorAll("input:checked")].map(x=>x.value),role=newRole.value;const r=await fetch("/dev-portal/api/admin/users",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({username:newUsername.value,display_name:newDisplayName.value,password:newPassword.value,is_admin:role==="admin",role,access_status:newAccessStatus.value,trial_start:newTrialStart.value||null,trial_end:newTrialEnd.value||null,dashboard_ids})});userCreateResult.textContent=r.ok?" ✓ angelegt":" ✗ konnte nicht angelegt werden";if(r.ok){userCreate.reset();syncRoleFields();loadUsers()}};
async function editUser(id){const r=await fetch("/dev-portal/api/admin/users",{cache:"no-store"}),d=await r.json(),u=d.users.find(x=>x.id===id);if(!u)return;const name=prompt("Name",u.display_name);if(name===null)return;const password=prompt("Neues Passwort (leer = unverändert)","");if(password===null)return;const active=confirm("Benutzer aktiv lassen? OK = aktiv, Abbrechen = deaktivieren");const body={display_name:name,active,is_admin:!!u.is_admin,password:password||null,dashboard_ids:u.dashboard_ids};const x=await fetch("/dev-portal/api/admin/users/"+id,{method:"PUT",headers:{"content-type":"application/json"},body:JSON.stringify(body)});if(!x.ok)alert("Änderung fehlgeschlagen");loadUsers()}

async function sendHeatingCommand(body){const r=await fetch("/dev-portal/api/oschmalz/heating/command",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(body)});if(!r.ok)throw Error(await r.text());setTimeout(loadOschmalzHeating,500)}
document.querySelectorAll(".mode-switch").forEach(group=>group.querySelectorAll("button").forEach(button=>button.onclick=async()=>{try{if(group.dataset.zone==="living"){await sendHeatingCommand({mode:button.dataset.mode==="on"?"HEIZEN":button.dataset.mode==="off"?"AUS":"ECO"})}else{await sendHeatingCommand({[group.dataset.zone]:button.dataset.mode==="on"})}}catch(e){alert("Befehl konnte nicht gesendet werden.")}}));
livingSetpoint.addEventListener("change",()=>sendHeatingCommand({comfort_temperature:Number(livingSetpoint.value)}).catch(()=>alert("Solltemperatur konnte nicht gespeichert werden.")));
livingEcoSetpoint.addEventListener("change",()=>sendHeatingCommand({eco_temperature:Number(livingEcoSetpoint.value)}).catch(()=>alert("ECO-Temperatur konnte nicht gespeichert werden.")));

const mobileMenu=document.getElementById("mobileMenu"),portalNav=document.getElementById("portalNav"),navBackdrop=document.getElementById("navBackdrop");
function closeMobileNav(){document.body.classList.remove("nav-open")}
mobileMenu?.addEventListener("click",()=>document.body.classList.toggle("nav-open"));
navBackdrop?.addEventListener("click",closeMobileNav);
portalNav?.querySelectorAll("button").forEach(button=>button.addEventListener("click",()=>{if(window.innerWidth<=720)closeMobileNav()}));
window.addEventListener("resize",()=>{if(window.innerWidth>720)closeMobileNav()});

function fmt1(v,u=""){return v===null||v===undefined?"–":(Math.round(Number(v)*10)/10)+u}
async function loadOschmalzHeating(){
  if(!document.getElementById("livingTemp"))return;
  try{
    const r=await fetch("/dev-portal/api/oschmalz/heating?t="+Date.now(),{cache:"no-store"});
    if(!r.ok)return;
    const d=await r.json(),v=d.values||{};
    livingTemp.textContent=fmt1(v.temperature," °C");
    livingState.textContent=d.online?(v.mode||"–"):"Keine Verbindung";
    livingState.classList.toggle("ok",!!d.online);
    if(document.activeElement!==livingSetpoint&&v.comfort_temperature!=null)livingSetpoint.value=v.comfort_temperature;
    if(document.activeElement!==livingEcoSetpoint&&v.eco_temperature!=null)livingEcoSetpoint.value=v.eco_temperature;
    document.querySelectorAll('.mode-switch[data-zone="living"] button').forEach(b=>b.classList.toggle("active",b.dataset.mode===(v.mode==="HEIZEN"?"on":v.mode==="AUS"?"off":String(v.mode||"").toLowerCase())));
    ecoSetting.classList.toggle("hidden",v.mode!=="ECO");
    const states={bedroom:v.bedroom?.state,bathroom:v.bathroom?.state};
    Object.entries(states).forEach(([zone,state])=>document.querySelectorAll(`.mode-switch[data-zone="${zone}"] button`).forEach(b=>b.classList.toggle("active",b.dataset.mode===state)));
  }catch(e){livingState.textContent="Keine Verbindung"}
}
loadOschmalzHeating();setInterval(loadOschmalzHeating,5000);

logoutButton?.addEventListener("click",async()=>{try{await fetch("/dev-portal/api/logout",{method:"POST"})}finally{document.body.classList.remove("nav-open");portalView.classList.add("hidden");loginView.classList.remove("hidden");loginPassword.value="";loginUser.focus()}});

let oekofenPlant=null,oekofenLiveTimer=null,oekofenPeriod="24h";
const escp=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function liveGroup(rows,title,match){const items=rows.filter(x=>match.test(x.label));if(!items.length)return"";return '<article class="card oek-live-card"><h2>'+title+'</h2>'+items.map(x=>'<div class="oek-live-row"><span>'+escp(x.label)+'</span><strong>'+escp(x.actual||"–")+(x.target?'<small>Soll '+escp(x.target)+'</small>':"")+'</strong></div>').join("")+'</article>'}
function oekQuery(){return oekofenPlant?.plant_id?"&plant_id="+encodeURIComponent(oekofenPlant.plant_id):""}
async function loadOekofenPortal(){
 try{
  const meResponse=await fetch("/dev-portal/api/me",{cache:"no-store"});
  if(!meResponse.ok)throw Error("HTTP "+meResponse.status);
  const me=await meResponse.json();
  if(me.is_admin){
   const pr=await fetch("/dev-portal/api/oekofen/plants",{cache:"no-store"});
   if(pr.ok){
    const pd=await pr.json();
    const picker=document.getElementById("oekofenAdminPicker");
    const sel=document.getElementById("oekofenPlantSelect");
    const search=document.getElementById("oekofenPlantSearch");
    picker.classList.remove("hidden");
    const renderPlants=()=>{
     const q=(search.value||"").trim().toLowerCase();
     const filtered=pd.plants.filter(p=>!q||[p.plant_name,p.customer_name,p.customer_city,p.serial_number,p.plant_id].some(v=>String(v||"").toLowerCase().includes(q)));
     const current=sel.value||localStorage.getItem("ins-dev-oekofen-plant")||"";
     sel.innerHTML=filtered.map(p=>'<option value="'+escp(p.plant_id)+'">'+escp(p.plant_name||p.plant_id)+(p.customer_name?' · '+escp(p.customer_name):'')+(p.customer_city?' · '+escp(p.customer_city):'')+(p.serial_number?' · '+escp(p.serial_number):'')+'</option>').join("");
     if(filtered.some(p=>p.plant_id===current))sel.value=current;
    };
    renderPlants();
    const saved=localStorage.getItem("ins-dev-oekofen-plant");
    if(saved&&pd.plants.some(p=>p.plant_id===saved))sel.value=saved;
    sel.onchange=async()=>{
     if(!sel.value)return;
     localStorage.setItem("ins-dev-oekofen-plant",sel.value);
     const p=pd.plants.find(x=>x.plant_id===sel.value);
     oekofenPlant={plant_id:p.plant_id,name:p.plant_name,serial_number:p.serial_number,customer_name:p.customer_name};
     oekofenNav?.classList.remove("hidden");
     oekofenTitle.textContent=p.plant_name||"Meine Heizung";
     oekofenMeta.textContent=[p.serial_number,p.customer_name,p.customer_city].filter(Boolean).join(" · ");
     await refreshOekofenLive();
     await loadOekofenHistory();
    };
    search.oninput=()=>{
     renderPlants();
     if(sel.options.length===1){
      sel.selectedIndex=0;
      sel.onchange();
     }
    };
    oekofenNav?.classList.remove("hidden");
    if(sel.value)await sel.onchange();
    return;
   }
  }
  const r=await fetch("/dev-portal/api/oekofen",{cache:"no-store"});
  if(!r.ok){oekofenNav?.classList.add("hidden");return}
  const d=await r.json();
  oekofenPlant=d.plant;
  oekofenNav?.classList.remove("hidden");
  oekofenTitle.textContent=d.plant.name||"Meine Heizung";
  oekofenMeta.textContent=[d.plant.serial_number,d.plant.customer_name].filter(Boolean).join(" · ");
  await refreshOekofenLive();
  await loadOekofenHistory();
 }catch(e){
  console.error("OekoFEN DEV portal",e);
  oekofenNav?.classList.add("hidden");
 }
}
async function refreshOekofenLive(){
 if(!oekofenPlant)return;
 try{
  const r=await fetch("/dev-portal/api/oekofen/live?t="+Date.now()+oekQuery(),{cache:"no-store"});if(!r.ok)throw Error(r.status);const d=await r.json(),rows=d.rows||[];
  oekofenOnline.textContent="● Online";oekofenOnline.classList.add("ok");
  const groups=[
   liveGroup(rows,"Übersicht",/Außentemperatur|Akt\. Temperatur|Bedienteil/),
   liveGroup(rows,"Kessel",/Kesseltemperatur|Brenneranforderung|Kesselstatus|Modulation|Abgastemperatur|Feuerraumtemperatur|Pelletfüllstand|Aschemenge/),
   liveGroup(rows,"Warmwasser",/^WW/),
   liveGroup(rows,"Puffer",/^PU/),
   liveGroup(rows,"Heizkreis 1",/^HK1/),
   liveGroup(rows,"Heizkreis 2",/^HK2/)
  ].filter(Boolean);
  oekofenLive.innerHTML=groups.join("")||'<div class="card">Keine aktuellen Messwerte verfügbar.</div>';
 }catch(e){oekofenOnline.textContent="● Keine Verbindung";oekofenOnline.classList.remove("ok")}
}
function friendlyField(name){
 const map=[
  [/^AT\[°C\]$/,"Außentemperatur"],[/^ATakt\[°C\]$/,"Außentemperatur aktuell"],
  [/PE1 KT\[°C\]/,"Kesseltemperatur"],[/PE1 KT_SOLL\[°C\]/,"Kessel Soll"],
  [/PU1 TPO Ist\[°C\]/,"Puffer oben"],[/PU1 TPM Ist\[°C\]/,"Puffer Mitte"],
  [/PU1 TPO Soll\[°C\]/,"Puffer oben Soll"],[/PU1 TPM Soll\[°C\]/,"Puffer Mitte Soll"],
  [/WW1 EinT Ist\[°C\]/,"Warmwasser"],[/WW1 Soll\[°C\]/,"Warmwasser Soll"],
  [/HK1 VL Ist\[°C\]/,"Vorlauf"],[/HK1 VL Soll\[°C\]/,"Vorlauf Soll"],
  [/HK2 VL Ist\[°C\]/,"Vorlauf"],[/HK2 VL Soll\[°C\]/,"Vorlauf Soll"]
 ];for(const [re,label] of map)if(re.test(name))return label;return name.replace(/\[°C\]/g,"").replace(/ Ist/g,"");
}
function svgChart(title,series){
 const entries=Object.entries(series).filter(([,v])=>v.length);if(!entries.length)return"";
 const all=entries.flatMap(([,v])=>v.map(x=>x.value)).filter(Number.isFinite);if(!all.length)return"";
 let dataMin=Math.min(...all),dataMax=Math.max(...all),pad=Math.max(1,(dataMax-dataMin)*.12);
 let min=Math.floor((dataMin-pad)/5)*5,max=Math.ceil((dataMax+pad)/5)*5;if(max<=min)max=min+5;
 const W=760,H=250,L=48,R=16,T=18,B=34;
 const times=entries.flatMap(([,v])=>v.map(x=>Date.parse(x.time))).filter(Number.isFinite),t0=Math.min(...times),t1=Math.max(...times);
 const x=ts=>L+(ts-t0)/Math.max(1,t1-t0)*(W-L-R),y=v=>T+(max-v)/(max-min)*(H-T-B);
 const ticks=Array.from({length:5},(_,i)=>min+(max-min)*i/4);
 const grid=ticks.map(v=>'<line x1="'+L+'" y1="'+y(v)+'" x2="'+(W-R)+'" y2="'+y(v)+'" class="chart-grid"/><text x="'+(L-7)+'" y="'+(y(v)+3)+'" class="chart-axis" text-anchor="end">'+v.toFixed(v%1?1:0)+'°</text>').join("");
 const paths=entries.map(([name,pts],idx)=>{const d=pts.map((p,j)=>(j?"L":"M")+x(Date.parse(p.time)).toFixed(1)+" "+y(p.value).toFixed(1)).join(" ");return '<path class="chart-line line-'+(idx%6)+'" data-series="'+idx+'" d="'+d+'" fill="none"/>'}).join("");
 const labels=Array.from({length:5},(_,i)=>{const ts=t0+(t1-t0)*i/4,d=new Date(ts);return '<text x="'+x(ts)+'" y="'+(H-8)+'" class="chart-axis" text-anchor="'+(i===0?"start":i===4?"end":"middle")+'">'+d.toLocaleString("de-AT",{day:t1-t0>86400000?"2-digit":undefined,month:t1-t0>86400000?"2-digit":undefined,hour:"2-digit",minute:"2-digit"})+'</text>'}).join("");
 const id="chart-"+Math.random().toString(36).slice(2);
 setTimeout(()=>bindChartHover(id,entries,{W,H,L,R,T,B,min,max,t0,t1}),0);
 return '<article class="card chart-card" id="'+id+'"><div class="chart-head"><h2>'+title+'</h2><div class="chart-legend">'+entries.map(([n],i)=>'<span class="line-'+(i%6)+'">'+escp(friendlyField(n))+'</span>').join("")+'</div></div><div class="chart-svg-wrap"><svg viewBox="0 0 '+W+' '+H+'">'+grid+paths+labels+'<line class="chart-hover-line" x1="0" y1="'+T+'" x2="0" y2="'+(H-B)+'"/><circle class="chart-hover-dot" cx="0" cy="0" r="4"/></svg><div class="chart-tooltip"></div></div></article>'
}
function bindChartHover(id,entries,cfg){
 const card=document.getElementById(id);if(!card)return;const svg=card.querySelector("svg"),tip=card.querySelector(".chart-tooltip"),line=card.querySelector(".chart-hover-line"),dot=card.querySelector(".chart-hover-dot");
 svg.addEventListener("mousemove",e=>{const r=svg.getBoundingClientRect(),px=(e.clientX-r.left)/r.width*cfg.W,ratio=Math.max(0,Math.min(1,(px-cfg.L)/(cfg.W-cfg.L-cfg.R))),target=cfg.t0+ratio*(cfg.t1-cfg.t0);let best=null;
  entries.forEach(([name,pts],idx)=>pts.forEach(p=>{const ts=Date.parse(p.time),dist=Math.abs(ts-target);if(!best||dist<best.dist)best={name,p,idx,dist}}));if(!best)return;
  const ts=Date.parse(best.p.time),cx=cfg.L+(ts-cfg.t0)/Math.max(1,cfg.t1-cfg.t0)*(cfg.W-cfg.L-cfg.R),cy=cfg.T+(cfg.max-best.p.value)/(cfg.max-cfg.min)*(cfg.H-cfg.T-cfg.B);
  line.setAttribute("x1",cx);line.setAttribute("x2",cx);dot.setAttribute("cx",cx);dot.setAttribute("cy",cy);line.classList.add("show");dot.classList.add("show");
  tip.innerHTML='<strong>'+escp(friendlyField(best.name))+'</strong><br>'+Number(best.p.value).toFixed(1)+' °C<br><small>'+new Date(ts).toLocaleString("de-AT",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"})+'</small>';tip.classList.add("show");tip.style.left=Math.min(r.width-150,Math.max(8,e.clientX-r.left+12))+"px";tip.style.top=Math.max(8,e.clientY-r.top-55)+"px";
 });svg.addEventListener("mouseleave",()=>{tip.classList.remove("show");line.classList.remove("show");dot.classList.remove("show")});
}
async function loadOekofenHistory(){
 if(!oekofenPlant)return;oekofenCharts.innerHTML='<div class="card">Historie wird geladen …</div>';
 try{const r=await fetch("/dev-portal/api/oekofen/history?period="+oekofenPeriod+oekQuery(),{cache:"no-store"});if(!r.ok)throw Error(r.status);const d=await r.json(),s=d.series||{};
 const pick=re=>Object.fromEntries(Object.entries(s).filter(([k])=>re.test(k)));
 const tempOnly=obj=>Object.fromEntries(Object.entries(obj).filter(([k])=>/\[°C\]/.test(k)&&!/Status|Pumpe/.test(k)));
 const cards=[svgChart("Kessel & Außentemperatur",tempOnly(pick(/AT|Kessel|PE1 KT/))),svgChart("Puffer",tempOnly(pick(/PU1/))),svgChart("Warmwasser",tempOnly(pick(/WW1/))),svgChart("Heizkreis 1",tempOnly(pick(/HK1 VL/))),svgChart("Heizkreis 2",tempOnly(pick(/HK2 VL/)))].filter(Boolean);
 oekofenCharts.innerHTML=cards.join("")||'<div class="card">Für diesen Zeitraum sind noch keine historischen Daten vorhanden.</div>'}catch(e){oekofenCharts.innerHTML='<div class="card">Historie konnte nicht geladen werden.</div>'}
}
document.querySelectorAll("[data-oek-period]").forEach(b=>b.onclick=()=>{document.querySelectorAll("[data-oek-period]").forEach(x=>x.classList.toggle("active",x===b));oekofenPeriod=b.dataset.oekPeriod;loadOekofenHistory()});
document.querySelector('nav button[data-page="oekofen"]')?.addEventListener("click",()=>{clearInterval(oekofenLiveTimer);refreshOekofenLive();oekofenLiveTimer=setInterval(()=>{if(document.getElementById("oekofen")?.classList.contains("active"))refreshOekofenLive();else clearInterval(oekofenLiveTimer)},45000)});


let builderData={users:[],source_types:[],oekofen_plants:[]},dashboardSources=[],dashboardBlocks=[],dashboardSignals=[],dashboardTemplates=[];
function sourceTypeName(id){return builderData.source_types.find(x=>x.id===id)?.name||id}
let dashboardAccess=[];
function renderAccess(){
 accessUser.innerHTML='<option value="">Benutzer auswählen …</option>'+builderData.users.filter(u=>!dashboardAccess.some(a=>a.user_id===u.id)).map(u=>'<option value="'+u.id+'">'+escp(u.display_name)+' · '+escp(u.username)+(u.is_admin?' · Admin':'')+'</option>').join("");
 configAccess.innerHTML=dashboardAccess.map((a,i)=>{const u=builderData.users.find(x=>x.id===a.user_id)||{};return '<div class="access-row"><span><strong>'+escp(u.display_name||u.username)+'</strong><small>'+escp(u.username||"")+(u.is_admin?' · Admin':'')+'</small></span><span class="access-right">'+(a.permission==="write"?"Lesen & Schreiben":"Lesen")+'</span><button data-access-del="'+i+'" type="button">×</button></div>'}).join("");
 configAccess.querySelectorAll("[data-access-del]").forEach(b=>b.onclick=()=>{dashboardAccess.splice(+b.dataset.accessDel,1);renderAccess()});
}
function renderSources(){configSources.innerHTML=dashboardSources.map((x,i)=>'<div class="source-row"><select data-si="'+i+'" data-p="type">'+builderData.source_types.map(t=>'<option value="'+t.id+'" '+(t.id===x.type?'selected':'')+'>'+escp(t.name)+'</option>').join("")+'</select><input data-si="'+i+'" data-p="name" value="'+escp(x.name||"")+'" placeholder="Bezeichnung">'+(x.type==="oekofen"?'<select data-si="'+i+'" data-p="ref"><option value="">ÖkoFEN Anlage …</option>'+builderData.oekofen_plants.map(p=>'<option value="'+escp(p.plant_id)+'" '+(p.plant_id===x.ref?'selected':'')+'>'+escp(p.plant_name||p.plant_id)+'</option>').join("")+'</select>':'<input data-si="'+i+'" data-p="ref" value="'+escp(x.ref||"")+'" placeholder="Instanz / Referenz">')+'<button data-source-samples="'+i+'" type="button">Signale laden</button><button data-source-del="'+i+'" type="button">×</button></div>').join("");
 configSources.querySelectorAll("[data-p]").forEach(el=>el.onchange=e=>{const i=+e.target.dataset.si,p=e.target.dataset.p;dashboardSources[i][p]=e.target.value;if(p==="type"){dashboardSources[i].ref="";renderSources()}});
 configSources.querySelectorAll("[data-source-del]").forEach(b=>b.onclick=()=>{dashboardSources.splice(+b.dataset.sourceDel,1);renderSources()});
 configSources.querySelectorAll("[data-source-samples]").forEach(b=>b.onclick=async()=>{const src=dashboardSources[+b.dataset.sourceSamples];if(!src.ref)return;b.disabled=true;try{const d=await fetch("/dev-portal/api/admin/source-samples?source_type="+encodeURIComponent(src.type)+"&source_ref="+encodeURIComponent(src.ref),{cache:"no-store"}).then(r=>r.json());src.signals=d.signals||[];dashboardSignals=dashboardSources.flatMap((x,si)=>(x.signals||[]).map(v=>({...v,source_index:si,source_name:x.name||sourceTypeName(x.type)})));renderConfigBlocks()}finally{b.disabled=false}});
}
function signalOptions(current){return '<option value="">Signal auswählen …</option>'+dashboardSignals.map((x,i)=>'<option value="'+i+'" '+(String(i)===String(current)?'selected':'')+'>'+escp(x.source_name)+' · '+escp(x.label||x.key)+' · Sample: '+escp(x.sample??"–")+'</option>').join("")}
function renderConfigBlocks(){configBlocks.innerHTML=dashboardBlocks.map((b,bi)=>'<article class="config-block" data-bi="'+bi+'"><div class="config-block-head"><strong>'+(b.type==="card"?"Karte":"Graph")+'</strong><input data-title value="'+escp(b.title)+'"><div class="block-actions"><button data-save-template="'+bi+'" type="button">Als Template speichern</button><button data-remove="'+bi+'" type="button">Entfernen</button></div></div><div>'+b.items.map((it,ii)=>{const sig=dashboardSignals[it.signal]||{};return '<div class="config-signal"><select data-signal="'+ii+'">'+signalOptions(it.signal)+'</select><input data-label="'+ii+'" value="'+escp(it.label||"")+'" placeholder="Bezeichnung"><span class="signal-sample"><b>Sample</b> '+escp(sig.sample??"–")+(sig.raw!=null?'<small>Rohwert: '+escp(sig.raw)+'</small>':'')+'</span>'+(b.type==="chart"?'<select data-display="'+ii+'"><option value="line">Kurve</option><option value="binary" '+(it.display==="binary"?"selected":"")+'>EIN/AUS</option><option value="percent" '+(it.display==="percent"?"selected":"")+'>0–100 %</option><option value="percent_binary" '+(it.display==="percent_binary"?"selected":"")+'>0/100 → EIN/AUS</option><option value="auto" '+(it.display==="auto"?"selected":"")+'>Automatisch</option></select>':'')+'<button data-del="'+ii+'" type="button">×</button></div>'}).join("")+'</div><button data-add="'+bi+'" type="button">+ Wert</button></article>').join("");
 configBlocks.querySelectorAll(".config-block").forEach(el=>{const bi=+el.dataset.bi;el.querySelector("[data-title]").oninput=e=>dashboardBlocks[bi].title=e.target.value;el.querySelector("[data-remove]").onclick=()=>{dashboardBlocks.splice(bi,1);renderConfigBlocks()};el.querySelector("[data-save-template]").onclick=async()=>{const block=dashboardBlocks[bi],name=prompt("Name des Templates",block.title);if(!name)return;const items=block.items.map(it=>{const sig=dashboardSignals[it.signal]||{};const src=dashboardSources[sig.source_index]||{};return {label:it.label,display:it.display||"line",source_type:src.type||null,scope:sig.scope||null,key:sig.key||null}});const types=[...new Set(items.map(x=>x.source_type).filter(Boolean))];const body={name,block_type:block.type,source_type:types.length===1?types[0]:null,template:{title:block.title,items}};const r=await fetch("/dev-portal/api/admin/dashboard-templates",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(body)});if(r.ok){await loadDashboardTemplates();alert("Template gespeichert.")}else alert("Template konnte nicht gespeichert werden.")};el.querySelector("[data-add]").onclick=()=>{dashboardBlocks[bi].items.push({signal:"",label:"",display:"line"});renderConfigBlocks()};el.querySelectorAll(".config-signal").forEach((row,ii)=>{row.querySelector("[data-signal]").onchange=e=>{dashboardBlocks[bi].items[ii].signal=e.target.value;const sig=dashboardSignals[e.target.value];if(sig&&!dashboardBlocks[bi].items[ii].label)dashboardBlocks[bi].items[ii].label=sig.label;renderConfigBlocks()};row.querySelector("[data-label]").oninput=e=>dashboardBlocks[bi].items[ii].label=e.target.value;row.querySelector("[data-display]")?.addEventListener("change",e=>dashboardBlocks[bi].items[ii].display=e.target.value);row.querySelector("[data-del]").onclick=()=>{dashboardBlocks[bi].items.splice(ii,1);renderConfigBlocks()}})})}
async function loadDashboardTemplates(){try{const d=await fetch("/dev-portal/api/admin/dashboard-templates",{cache:"no-store"}).then(r=>r.json());dashboardTemplates=d.templates||[];templateSelect.innerHTML='<option value="">Template auswählen …</option>'+dashboardTemplates.map(t=>'<option value="'+t.id+'">'+escp(t.name)+' · '+(t.block_type==="card"?"Karte":"Graph")+(t.source_type?' · '+escp(sourceTypeName(t.source_type)):'')+'</option>').join("")}catch(e){}}
async function initDashboardConfigurator(){try{builderData=await fetch("/dev-portal/api/admin/dashboard-builder-data",{cache:"no-store"}).then(r=>r.json());renderAccess();renderSources();await loadDashboardTemplates()}catch(e){}}
addAccess.onclick=()=>{const id=Number(accessUser.value);if(!id||dashboardAccess.some(x=>x.user_id===id))return;dashboardAccess.push({user_id:id,permission:accessPermission.value});renderAccess()};
addSource.onclick=()=>{dashboardSources.push({type:"oekofen",name:"",ref:"",signals:[]});renderSources()};
addCard.onclick=()=>{dashboardBlocks.push({type:"card",title:"Neue Karte",items:[]});renderConfigBlocks()};
addChart.onclick=()=>{dashboardBlocks.push({type:"chart",title:"Neuer Graph",items:[]});renderConfigBlocks()};
addTemplate.onclick=()=>{const t=dashboardTemplates.find(x=>String(x.id)===String(templateSelect.value));if(!t)return;const items=(t.template.items||[]).map(item=>{let signal="";const idx=dashboardSignals.findIndex(sig=>{const src=dashboardSources[sig.source_index]||{};return src.type===item.source_type&&sig.scope===item.scope&&sig.key===item.key});if(idx>=0)signal=String(idx);return {signal,label:item.label||"",display:item.display||"line"};});dashboardBlocks.push({type:t.block_type,title:t.template.title||t.name,items});renderConfigBlocks()};
function renderDashboardPreview(){
 previewTitle.textContent=configName.value||"Dashboard Vorschau";
 const cards=dashboardBlocks.filter(b=>b.type==="card").map(b=>'<article class="card preview-card"><h2>'+escp(b.title)+'</h2>'+b.items.map(it=>{const sig=dashboardSignals[it.signal]||{};return '<div class="oek-live-row"><span>'+escp(it.label||sig.label||sig.key||"Wert")+'</span><strong>'+escp(sig.sample??"–")+'</strong></div>'}).join("")+'</article>').join("");
 const charts=dashboardBlocks.filter(b=>b.type==="chart").map(b=>'<article class="card preview-chart"><h2>'+escp(b.title)+'</h2><div class="preview-chart-lines">'+b.items.map(it=>{const sig=dashboardSignals[it.signal]||{};return '<div><span>'+escp(it.label||sig.label||sig.key||"Signal")+'</span><strong>'+escp(sig.sample??"–")+'</strong><small>'+escp(it.display==="binary"?"EIN/AUS":it.display==="percent"?"0–100 %":it.display==="percent_binary"?"0/100 → EIN/AUS":it.display==="auto"?"Automatisch":"Kurve")+'</small></div>'}).join("")+'</div><div class="preview-chart-placeholder">Historischer Verlauf wird im echten Dashboard aus der Datenquelle geladen.</div></article>').join("");
 previewDashboardBody.innerHTML='<div class="preview-summary">'+(configDescription.value?'<p>'+escp(configDescription.value)+'</p>':'')+'</div><div class="oek-live-grid">'+(cards||'<div class="card">Noch keine Karten definiert.</div>')+'</div>'+(charts?'<h2 class="preview-section-title">Verlauf</h2><div class="oek-charts">'+charts+'</div>':'');
 dashboardPreviewModal.classList.remove("hidden");
}
previewDashboard.onclick=renderDashboardPreview;
closeDashboardPreview.onclick=()=>dashboardPreviewModal.classList.add("hidden");
dashboardPreviewModal.onclick=e=>{if(e.target===dashboardPreviewModal)dashboardPreviewModal.classList.add("hidden")};
saveDashboardConfig.onclick=async()=>{const access=dashboardAccess.map(x=>({...x}));const sources=dashboardSources.map(x=>({type:x.type,name:x.name,ref:x.ref}));const blocks=dashboardBlocks.map(b=>({...b,items:b.items.map(it=>{const sig=dashboardSignals[it.signal]||{};return {...it,source_index:sig.source_index,scope:sig.scope,key:sig.key,sample:sig.sample,raw:sig.raw}})}));const body={name:configName.value,description:configDescription.value,active:true,access,sources,config:{blocks}};const r=await fetch("/dev-portal/api/admin/dashboard-configs",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(body)});configSaveResult.textContent=r.ok?" ✓ gespeichert":" ✗ Fehler"};
initDashboardConfigurator();

function syncRoleFields(){newCustomerAccess?.classList.toggle("hidden",newRole?.value!=="end_customer")}newRole?.addEventListener("change",syncRoleFields);syncRoleFields();
