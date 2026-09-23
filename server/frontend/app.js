const API="/api/v1/telemetry/status";
const val=(o,k,u="")=>o?.current?.[k]===undefined?"–":`${o.current[k]}${u}`;
function card(x){return `<article class="card"><div class="card-head"><div><h2>${x.installation_id}</h2><span class="status ${x.online?"online":"offline"}">● ${x.online?"Online":"Offline"}</span></div></div><div class="metrics">
<div class="metric"><small>Batterie SOC</small><strong>${val(x,"battery.soc"," %")}</strong></div>
<div class="metric"><small>Batterieleistung</small><strong>${val(x,"battery.power"," W")}</strong></div>
<div class="metric"><small>PV</small><strong>${val(x,"pv.power"," W")}</strong></div>
<div class="metric"><small>Netz</small><strong>${val(x,"grid.power"," W")}</strong></div>
<div class="metric"><small>Puffer oben</small><strong>${val(x,"buffer.temperature_upper"," °C")}</strong></div>
<div class="metric"><small>Warmwasser</small><strong>${val(x,"dhw.temperature"," °C")}</strong></div>
</div><div class="meta">Letzter Kontakt vor ${x.age_seconds}s · Samples: ${x.sample_count}<br>Optimierer: ${val(x,"shadow.action")}</div></article>`}
async function load(){try{const r=await fetch(API);if(!r.ok)throw Error(r.status);const d=await r.json();count.textContent=d.count;online.textContent=d.online;offline.textContent=d.count-d.online;cards.innerHTML=d.installations.map(card).join("")||'<div class="loading">Noch keine Instanzen.</div>';api.textContent="API online";api.classList.add("ok")}catch(e){api.textContent="API offline";cards.innerHTML='<div class="loading">INS-EI API nicht erreichbar.</div>'}}
load();setInterval(load,30000);