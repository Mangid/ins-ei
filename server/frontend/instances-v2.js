/* INS-EI Instanzen V2 – cockpit overview */
function insValue(x,key){return x?.current?.[key]}
function insHas(x,key){const v=insValue(x,key);return v!==undefined&&v!==null&&v!==""}
function insActionLabel(action){
  return ({POWER_TO_HEAT:"PV → Wärme",EXPORT_PV:"PV einspeisen",BALANCED:"Ausgeglichen",BATTERY_SUPPORT_LOAD:"Batterie versorgt Haus",BATTERY_SELL:"Batterie verkaufen"})[action]||action||"Keine Entscheidung"
}
function insActionReason(x){
  const action=insValue(x,"shadow.action"),soc=insValue(x,"battery.soc"),buf=insValue(x,"buffer.temperature_upper");
  if(action==="POWER_TO_HEAT")return "PV-Überschuss thermisch nutzen";
  if(action==="EXPORT_PV")return soc>=99&&buf!=null?`Batterie ${Math.round(soc)} % · Puffer ${fmt(buf," °C")} → einspeisen`:"PV-Überschuss einspeisen";
  if(action==="BALANCED")return "Anlage aktuell ausgeglichen";
  if(action==="BATTERY_SUPPORT_LOAD")return "Batterie vermeidet Netzbezug";
  return insValue(x,"shadow.reason")||"";
}
function insThermalLine(x){
  const parts=[];
  const boiler=insValue(x,"boiler_permission.recommendation")||insValue(x,"thermal.boiler_permission");
  const dhw=insValue(x,"dhw_transfer.recommendation")||insValue(x,"thermal.dhw_transfer");
  const strategy=insValue(x,"buffer.strategy")||insValue(x,"thermal.buffer_strategy");
  if(boiler)parts.push("Pellet "+boiler);
  if(dhw)parts.push("WW "+dhw);
  if(strategy)parts.push("Puffer "+strategy);
  return parts.length?`<div class="thermal-line">${parts.map(p=>`<span>${p}</span>`).join("")}</div>`:"";
}
function insWarnings(x){
  const out=[];
  if(!x.online)out.push("Instanz offline");
  if(x.online&&x.age_seconds>90)out.push("Telemetrie verspätet");
  const confidence=String(insValue(x,"shadow.confidence")||"").toUpperCase();
  if(confidence==="LOW")out.push("Optimierer: geringe Konfidenz");
  if(insHas(x,"forecast.pv_today")&&!insHas(x,"forecast.consumption_today"))out.push("Verbrauchsforecast fehlt");
  if(insHas(x,"pv.power")&&!insHas(x,"MARKET.spot_price")&&insValue(x,"shadow.action")!=="BALANCED")out.push("Marktpreis fehlt");
  return out;
}
card=function(x){
  const action=insValue(x,"shadow.action"),warnings=insWarnings(x);
  return `<article class="card clickable instance-card" data-id="${x.installation_id}">
    <div class="card-head"><div><h2>${x.installation_id}</h2><span class="status ${x.online?"online":"offline"}">● ${x.online?"Online":"Offline"}</span></div><div class="card-actions"><span class="action-badge action-${String(action||"unknown").toLowerCase()}">${insActionLabel(action)}</span><span class="open">Details →</span></div></div>
    <div class="metrics">${metric("Batterie SOC",cv(x,"battery.soc"," %"))}${metric("Batterieleistung",cv(x,"battery.power"," W"))}${metric("PV",cv(x,"pv.power"," W"))}${metric("Netz",cv(x,"grid.power"," W"))}${metric("Puffer oben",cv(x,"buffer.temperature_upper"," °C"))}${metric("Warmwasser",cv(x,"dhw.temperature"," °C"))}</div>
    <div class="decision"><strong>${insActionLabel(action)}</strong><span>${insActionReason(x)}</span></div>
    ${insThermalLine(x)}
    ${warnings.length?`<div class="card-warnings">${warnings.map(w=>`<span>⚠ ${w}</span>`).join("")}</div>`:""}
    <div class="meta">Letzter Kontakt vor ${x.age_seconds}s · Samples: ${x.sample_count}</div>
  </article>`
}
function renderInstanceAttention(){
  const section=document.getElementById("instanceAttention"),list=document.getElementById("attentionList"),countEl=document.getElementById("attentionCount");
  if(!section||!list||!latest.installations)return;
  const rows=latest.installations.flatMap(x=>insWarnings(x).map(w=>({x,w})));
  countEl.textContent=rows.length;
  section.classList.toggle("hidden",rows.length===0||document.getElementById("instances")?.classList.contains("hidden"));
  list.innerHTML=rows.map(({x,w})=>`<button class="attention-item" data-id="${x.installation_id}"><strong>${x.installation_id}</strong><span>${w}</span><b>Öffnen →</b></button>`).join("");
  list.querySelectorAll(".attention-item").forEach(b=>b.onclick=()=>openDetail(b.dataset.id));
}
const insOriginalLoad=load;
load=async function(){await insOriginalLoad();renderInstanceAttention()}
const insOriginalSetView=setView;
setView=function(view){insOriginalSetView(view);const section=document.getElementById("instanceAttention");if(section){if(view==="instances")renderInstanceAttention();else section.classList.add("hidden")}}
setTimeout(()=>load(),0);
