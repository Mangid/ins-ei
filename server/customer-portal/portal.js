loginForm.onsubmit=async e=>{e.preventDefault();loginError.textContent="";const r=await fetch("/portal/api/login",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({username:loginUser.value,password:loginPassword.value})});if(!r.ok){loginError.textContent="Anmeldung fehlgeschlagen.";return}await load()};
const pages=[...document.querySelectorAll(".page")];document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{document.querySelectorAll("nav button").forEach(x=>x.classList.remove("active"));pages.forEach(x=>x.classList.remove("active"));b.classList.add("active");document.getElementById(b.dataset.page).classList.add("active")});
const metric=(name,value)=>'<div class="card metric"><small>'+name+'</small><strong>'+value+'</strong></div>';
async function load(){try{const r=await fetch("/portal/api/me",{cache:"no-store"});if(!r.ok)throw Error("HTTP "+r.status);const d=await r.json();loginView.classList.add("hidden");portalView.classList.remove("hidden");user.textContent=d.display_name||"Kunde";adminNav.classList.toggle("hidden",!d.is_admin);if(d.is_admin)loadUsers();summary.innerHTML=metric("Anlagen",d.systems.length)+metric("Status",d.status||"–");status.textContent=d.message||"Portal bereit."}catch(e){portalView.classList.add("hidden");loginView.classList.remove("hidden");}}load();
async function loadUsers(){const r=await fetch("/portal/api/admin/users",{cache:"no-store"});if(!r.ok)return;const d=await r.json();window.portalDashboards=d.dashboards;newDashboards.innerHTML=d.dashboards.map(x=>'<label><input type="checkbox" value="'+x.id+'"> '+x.name+'</label>').join(" ");userList.innerHTML=d.users.map(x=>'<div class="user-row"><div><strong>'+x.display_name+'</strong><small>'+x.username+(x.is_admin?" · Admin":"")+(x.active?"":" · deaktiviert")+'</small></div><div>'+x.dashboard_ids.map(id=>'<span class="tag">'+id+'</span>').join("")+'</div><button onclick="editUser('+x.id+')">Bearbeiten</button></div>').join("")}
userCreate.onsubmit=async e=>{e.preventDefault();const dashboard_ids=[...newDashboards.querySelectorAll("input:checked")].map(x=>x.value);const r=await fetch("/portal/api/admin/users",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({username:newUsername.value,display_name:newDisplayName.value,password:newPassword.value,is_admin:newAdmin.checked,dashboard_ids})});userCreateResult.textContent=r.ok?" ✓ angelegt":" ✗ konnte nicht angelegt werden";if(r.ok){userCreate.reset();loadUsers()}};
async function editUser(id){const r=await fetch("/portal/api/admin/users",{cache:"no-store"}),d=await r.json(),u=d.users.find(x=>x.id===id);if(!u)return;const name=prompt("Name",u.display_name);if(name===null)return;const password=prompt("Neues Passwort (leer = unverändert)","");if(password===null)return;const active=confirm("Benutzer aktiv lassen? OK = aktiv, Abbrechen = deaktivieren");const body={display_name:name,active,is_admin:!!u.is_admin,password:password||null,dashboard_ids:u.dashboard_ids};const x=await fetch("/portal/api/admin/users/"+id,{method:"PUT",headers:{"content-type":"application/json"},body:JSON.stringify(body)});if(!x.ok)alert("Änderung fehlgeschlagen");loadUsers()}

async function sendHeatingCommand(body){const r=await fetch("/portal/api/oschmalz/heating/command",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(body)});if(!r.ok)throw Error(await r.text());setTimeout(loadOschmalzHeating,500)}
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
    const r=await fetch("/portal/api/oschmalz/heating?t="+Date.now(),{cache:"no-store"});
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
