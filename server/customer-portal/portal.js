loginForm.onsubmit=async e=>{e.preventDefault();loginError.textContent="";const r=await fetch("api/login",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({username:loginUser.value,password:loginPassword.value})});if(!r.ok){loginError.textContent="Anmeldung fehlgeschlagen.";return}await load()};
const pages=[...document.querySelectorAll(".page")];document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{document.querySelectorAll("nav button").forEach(x=>x.classList.remove("active"));pages.forEach(x=>x.classList.remove("active"));b.classList.add("active");document.getElementById(b.dataset.page).classList.add("active")});
const metric=(name,value)=>'<div class="card metric"><small>'+name+'</small><strong>'+value+'</strong></div>';
async function load(){try{const r=await fetch("api/me",{cache:"no-store"});if(!r.ok)throw Error("HTTP "+r.status);const d=await r.json();loginView.classList.add("hidden");portalView.classList.remove("hidden");user.textContent=d.display_name||"Kunde";adminNav.classList.toggle("hidden",!d.is_admin);if(d.is_admin)loadUsers();loadOekofenPortal();if(!d.is_admin){document.querySelectorAll("nav button").forEach(x=>x.classList.remove("active"));pages.forEach(x=>x.classList.remove("active"));document.querySelector('nav button[data-page="systems"]')?.classList.add("active");document.getElementById("systems")?.classList.add("active")}summary.innerHTML=metric("Anlagen",d.systems.length)+metric("Status",d.status||"–");status.textContent=d.message||"Portal bereit."}catch(e){portalView.classList.add("hidden");loginView.classList.remove("hidden");}}load();
async function loadUsers(){const r=await fetch("api/admin/users",{cache:"no-store"});if(!r.ok)return;const d=await r.json();window.portalDashboards=d.dashboards;newDashboards.innerHTML=d.dashboards.map(x=>'<label><input type="checkbox" value="'+x.id+'"> '+x.name+'</label>').join(" ");userList.innerHTML=d.users.map(x=>'<div class="user-row"><div><strong>'+x.display_name+'</strong><small>'+x.username+(x.is_admin?" · Admin":"")+(x.active?"":" · deaktiviert")+'</small></div><div>'+x.dashboard_ids.map(id=>'<span class="tag">'+id+'</span>').join("")+'</div><button onclick="editUser('+x.id+')">Bearbeiten</button></div>').join("")}
userCreate.onsubmit=async e=>{e.preventDefault();const dashboard_ids=[...newDashboards.querySelectorAll("input:checked")].map(x=>x.value);const r=await fetch("api/admin/users",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({username:newUsername.value,display_name:newDisplayName.value,password:newPassword.value,is_admin:newAdmin.checked,dashboard_ids})});userCreateResult.textContent=r.ok?" ✓ angelegt":" ✗ konnte nicht angelegt werden";if(r.ok){userCreate.reset();loadUsers()}};
async function editUser(id){const r=await fetch("api/admin/users",{cache:"no-store"}),d=await r.json(),u=d.users.find(x=>x.id===id);if(!u)return;const name=prompt("Name",u.display_name);if(name===null)return;const password=prompt("Neues Passwort (leer = unverändert)","");if(password===null)return;const active=confirm("Benutzer aktiv lassen? OK = aktiv, Abbrechen = deaktivieren");const body={display_name:name,active,is_admin:!!u.is_admin,password:password||null,dashboard_ids:u.dashboard_ids};const x=await fetch("api/admin/users/"+id,{method:"PUT",headers:{"content-type":"application/json"},body:JSON.stringify(body)});if(!x.ok)alert("Änderung fehlgeschlagen");loadUsers()}

async function sendHeatingCommand(body){const r=await fetch("api/oschmalz/heating/command",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(body)});if(!r.ok)throw Error(await r.text());setTimeout(loadOschmalzHeating,500)}
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
    const r=await fetch("api/oschmalz/heating?t="+Date.now(),{cache:"no-store"});
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

logoutButton?.addEventListener("click",async()=>{try{await fetch("api/logout",{method:"POST"})}finally{document.body.classList.remove("nav-open");portalView.classList.add("hidden");loginView.classList.remove("hidden");loginPassword.value="";loginUser.focus()}});

let oekofenPlant=null,oekofenLiveTimer=null,oekofenPeriod="24h";
const escp=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function liveGroup(rows,title,match){const items=rows.filter(x=>match.test(x.label));if(!items.length)return"";return '<article class="card oek-live-card"><h2>'+title+'</h2>'+items.map(x=>'<div class="oek-live-row"><span>'+escp(x.label)+'</span><strong>'+escp(x.actual||"–")+(x.target?'<small>Soll '+escp(x.target)+'</small>':"")+'</strong></div>').join("")+'</article>'}
async function loadOekofenPortal(){
 try{
  const r=await fetch("api/oekofen",{cache:"no-store"});if(!r.ok){oekofenNav?.classList.add("hidden");return}
  const d=await r.json();oekofenPlant=d.plant;oekofenNav?.classList.remove("hidden");oekofenTitle.textContent=d.plant.name||"Meine Heizung";oekofenMeta.textContent=[d.plant.serial_number,d.plant.customer_name].filter(Boolean).join(" · ");
  await refreshOekofenLive();await loadOekofenHistory();
 }catch(e){oekofenNav?.classList.add("hidden")}
}
async function refreshOekofenLive(){
 if(!oekofenPlant)return;
 try{
  const r=await fetch("api/oekofen/live?t="+Date.now(),{cache:"no-store"});if(!r.ok)throw Error(r.status);const d=await r.json(),rows=d.rows||[];
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
function svgChart(title,series){
 const entries=Object.entries(series).filter(([,v])=>v.length);if(!entries.length)return"";
 const all=entries.flatMap(([,v])=>v.map(x=>x.value));let min=Math.min(...all),max=Math.max(...all);if(max===min){max+=1;min-=1}
 const W=760,H=220,p=28;const times=entries.flatMap(([,v])=>v.map(x=>Date.parse(x.time))).filter(Number.isFinite);const t0=Math.min(...times),t1=Math.max(...times);
 const paths=entries.map(([name,pts],idx)=>{const d=pts.map((x,j)=>{const tx=Date.parse(x.time),px=p+(tx-t0)/Math.max(1,t1-t0)*(W-2*p),py=H-p-(x.value-min)/(max-min)*(H-2*p);return(j?"L":"M")+px.toFixed(1)+" "+py.toFixed(1)}).join(" ");return '<path class="chart-line line-'+(idx%6)+'" d="'+d+'" fill="none"/>'}).join("");
 return '<article class="card chart-card"><div class="chart-head"><h2>'+title+'</h2><div class="chart-legend">'+entries.map(([n],i)=>'<span class="line-'+(i%6)+'">'+escp(n)+'</span>').join("")+'</div></div><svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none">'+paths+'</svg><div class="chart-range"><span>'+min.toFixed(1)+'</span><span>'+max.toFixed(1)+'</span></div></article>'
}
async function loadOekofenHistory(){
 if(!oekofenPlant)return;oekofenCharts.innerHTML='<div class="card">Historie wird geladen …</div>';
 try{const r=await fetch("api/oekofen/history?period="+oekofenPeriod,{cache:"no-store"});if(!r.ok)throw Error(r.status);const d=await r.json(),s=d.series||{};
 const pick=re=>Object.fromEntries(Object.entries(s).filter(([k])=>re.test(k)));
 const cards=[svgChart("Kessel & Außentemperatur",pick(/AT|Kessel|PE1 KT/)),svgChart("Puffer",pick(/PU1/)),svgChart("Warmwasser",pick(/WW1/)),svgChart("Heizkreis 1",pick(/HK1 VL/)),svgChart("Heizkreis 2",pick(/HK2 VL/))].filter(Boolean);
 oekofenCharts.innerHTML=cards.join("")||'<div class="card">Für diesen Zeitraum sind noch keine historischen Daten vorhanden.</div>'}catch(e){oekofenCharts.innerHTML='<div class="card">Historie konnte nicht geladen werden.</div>'}
}
document.querySelectorAll("[data-oek-period]").forEach(b=>b.onclick=()=>{document.querySelectorAll("[data-oek-period]").forEach(x=>x.classList.toggle("active",x===b));oekofenPeriod=b.dataset.oekPeriod;loadOekofenHistory()});
document.querySelector('nav button[data-page="oekofen"]')?.addEventListener("click",()=>{clearInterval(oekofenLiveTimer);refreshOekofenLive();oekofenLiveTimer=setInterval(()=>{if(document.getElementById("oekofen")?.classList.contains("active"))refreshOekofenLive();else clearInterval(oekofenLiveTimer)},45000)});
