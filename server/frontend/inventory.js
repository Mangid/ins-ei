(()=>{
const $=id=>document.getElementById(id);
const esc=v=>String(v??"").replaceAll("&","&amp;").replaceAll('"',"&quot;").replaceAll("<","&lt;").replaceAll(">","&gt;");
const eur=v=>v===null||v===undefined||v===""?"–":Number(v).toLocaleString("de-AT",{style:"currency",currency:"EUR"});
const qty=(v,u)=>Number(v||0).toLocaleString("de-AT",{maximumFractionDigits:2})+" "+(u||"");
let items=[],locations=[];

function activate(){
  const isInventory=location.hash==="#inventory";
  $("inventory")?.classList.toggle("hidden",!isInventory);
  if(!isInventory)return;
  document.querySelectorAll("main>section").forEach(s=>s.classList.add("hidden"));
  $("inventory")?.classList.remove("hidden");
  document.querySelectorAll("#sidebar nav a").forEach(a=>a.classList.toggle("active",a.id==="navInventory"));
  if($("pageTitle"))$("pageTitle").textContent="Lager";
  if($("todayLabel"))$("todayLabel").textContent="Ersatzteile & Gebrauchsmaterial";
  load();
}
async function load(){
  const [ir,lr]=await Promise.all([fetch("/api/inventory/items?t="+Date.now(),{cache:"no-store"}),fetch("/api/inventory/locations?t="+Date.now(),{cache:"no-store"})]);
  if(!ir.ok||!lr.ok){$("inventoryRows").innerHTML='<tr><td colspan="8" class="loading">Lager konnte nicht geladen werden.</td></tr>';return}
  items=await ir.json();locations=await lr.json();render();
}
function render(){
  const q=($("inventorySearch")?.value||"").trim().toLowerCase();
  const filtered=items.filter(x=>!q||[x.article_number,x.name,x.manufacturer,x.category,x.supplier,x.sevdesk_article_number].some(v=>String(v||"").toLowerCase().includes(q)));
  const low=items.filter(x=>x.needs_reorder);
  $("inventoryCount").textContent=filtered.length+" Artikel";
  $("inventoryReorderCount").textContent=low.length+" nachzubestellen";
  $("inventorySummary").innerHTML='<article><b>'+items.length+'</b><span>Artikel</span></article><article><b>'+low.length+'</b><span>Nachbestellen</span></article><article><b>'+locations.length+'</b><span>Lagerorte</span></article>';
  $("inventoryRows").innerHTML=filtered.map(x=>'<tr class="'+(x.needs_reorder?'inventory-low':'')+'"><td><strong>'+esc(x.article_number||x.sevdesk_article_number||"–")+'</strong></td><td>'+esc(x.name)+'<small>'+esc([x.manufacturer,x.category].filter(Boolean).join(" · "))+'</small></td><td>'+x.stocks.map(s=>'<span class="stock-chip">'+esc(s.name)+': '+qty(s.quantity,x.unit)+'</span>').join("")+'</td><td><strong>'+qty(x.total_stock,x.unit)+'</strong></td><td>'+qty(x.minimum_stock,x.unit)+'</td><td>'+eur(x.purchase_price_net)+'</td><td><strong>'+eur(x.sales_price_net)+'</strong></td><td>'+(x.needs_reorder?'<span class="reorder-badge">Nachbestellen '+(x.suggested_order_quantity?qty(x.suggested_order_quantity,x.unit):"")+'</span>':'<span class="stock-ok">OK</span>')+'</td><td><div class="inventory-actions"><button class="secondary inventory-edit" data-id="'+x.id+'">Bearbeiten</button><button class="secondary inventory-move" data-id="'+x.id+'">Buchen</button></div></td></tr>').join("")||'<tr><td colspan="9" class="loading">Noch keine Artikel erfasst.</td></tr>';
  document.querySelectorAll(".inventory-move").forEach(b=>b.onclick=()=>movementForm(items.find(x=>String(x.id)===b.dataset.id)));
  document.querySelectorAll(".inventory-edit").forEach(b=>b.onclick=()=>itemForm(items.find(x=>String(x.id)===b.dataset.id)));
}
async function movementForm(item){
  if(!locations.length){alert("Bitte zuerst mindestens einen Lagerort anlegen.");return}
  let customers=[];try{const r=await fetch("/api/v1/customers");if(r.ok)customers=(await r.json()).customers||[]}catch(_){}
  const locOptions=locations.map(x=>'<option value="'+x.id+'">'+esc(x.name)+'</option>').join("");
  show('<div class="detail inventory-modal"><div class="detail-head"><div><h1>Bestand buchen</h1><span class="meta">'+esc(item.article_number||"")+' · '+esc(item.name)+'</span></div></div><form id="inventoryMovementForm" class="customer-form"><label>Vorgang<select name="movement_type" id="inventoryMovementType"><option value="receipt">Zugang</option><option value="issue">Entnahme</option><option value="transfer">Umlagerung</option></select></label><div class="form-row"><label id="inventoryFromWrap">Von Lager<select name="from_location_id"><option value="">–</option>'+locOptions+'</select></label><label id="inventoryToWrap">Nach Lager<select name="to_location_id"><option value="">–</option>'+locOptions+'</select></label></div><label>Menge<input name="quantity" type="number" min="0.01" step="0.01" value="1" required></label><label id="inventoryCustomerWrap">Kunde (optional)<select name="customer_id"><option value="">– keine Zuordnung –</option>'+customers.map(x=>'<option value="'+x.id+'">'+esc(x.name)+'</option>').join("")+'</select></label><label>Notiz<input name="note" placeholder="optional"></label><div class="form-actions"><button type="button" class="secondary" id="inventoryCancel">Abbrechen</button><button class="primary">Buchen</button></div></form></div>');
  const form=$("inventoryMovementForm"),type=$("inventoryMovementType");
  const paint=()=>{const t=type.value;$("inventoryFromWrap").classList.toggle("hidden",t==="receipt");$("inventoryToWrap").classList.toggle("hidden",t==="issue");$("inventoryCustomerWrap").classList.toggle("hidden",t!=="issue")};type.onchange=paint;paint();$("inventoryCancel").onclick=close;
  form.onsubmit=async e=>{e.preventDefault();const d=Object.fromEntries(new FormData(form).entries());d.item_id=item.id;d.quantity=Number(d.quantity);["from_location_id","to_location_id","customer_id"].forEach(k=>d[k]=d[k]?Number(d[k]):null);if(d.movement_type!=="issue")d.customer_id=null;const r=await fetch("/api/inventory/movements",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(d)});if(r.ok){close();load()}else{const x=await r.json().catch(()=>({}));alert(x.detail||"Bestandsbuchung fehlgeschlagen.")}};
}
function show(html){$("inventoryDialog").innerHTML=html;$("inventoryDialog").classList.remove("hidden")}
function close(){$("inventoryDialog").classList.add("hidden");$("inventoryDialog").innerHTML=""}

$("navInventory")?.addEventListener("click",()=>setTimeout(activate,0));
window.addEventListener("hashchange",()=>setTimeout(activate,0));
$("inventorySearch")?.addEventListener("input",render);
$("newInventoryLocation")?.addEventListener("click",()=>{
 show('<div class="detail inventory-modal"><div class="detail-head"><div><h1>Lagerort anlegen</h1><span class="meta">z. B. HTZ oder Bus</span></div></div><form id="inventoryLocationForm" class="customer-form"><label>Name<input name="name" required autofocus></label><label>Kürzel<input name="code" placeholder="optional"></label><div class="form-actions"><button type="button" class="secondary" id="inventoryCancel">Abbrechen</button><button class="primary">Anlegen</button></div></form></div>');
 $("inventoryCancel").onclick=close;$("inventoryLocationForm").onsubmit=async e=>{e.preventDefault();const d=Object.fromEntries(new FormData(e.currentTarget).entries());if(!d.code)d.code=null;const r=await fetch("/api/inventory/locations",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(d)});if(r.ok){close();load()}else alert("Lagerort konnte nicht angelegt werden.")};
});
function itemForm(item=null){
 const suggestions=key=>[...new Set(items.map(x=>String(x[key]||"").trim()).filter(Boolean))].sort((a,b)=>a.localeCompare(b,"de"));
 const list=(id,values)=>'<datalist id="'+id+'">'+values.map(v=>'<option value="'+esc(v)+'"></option>').join("")+'</datalist>';
 const val=(k)=>esc(item?.[k]??"");
 show('<div class="detail inventory-modal"><div class="detail-head"><div><h1>'+(item?"Artikel bearbeiten":"Artikel anlegen")+'</h1><span class="meta">Lager + sevdesk Referenz</span></div></div><form id="inventoryItemForm" class="customer-form"><div class="form-row"><label>Artikelnummer<input name="article_number" value="'+val("article_number")+'"></label><label>sevdesk Artikelnummer<input name="sevdesk_article_number" value="'+val("sevdesk_article_number")+'"></label></div><label>Bezeichnung<input name="name" required autofocus value="'+val("name")+'"></label><div class="form-row"><label>Hersteller<input name="manufacturer" list="inventoryManufacturers" value="'+val("manufacturer")+'">'+list("inventoryManufacturers",suggestions("manufacturer"))+'</label><label>Lieferant<input name="supplier" list="inventorySuppliers" value="'+val("supplier")+'">'+list("inventorySuppliers",suggestions("supplier"))+'</label></div><div class="form-row"><label>Kategorie<input name="category" list="inventoryCategories" value="'+val("category")+'">'+list("inventoryCategories",suggestions("category"))+'</label><label>Einheit<input name="unit" value="'+esc(item?.unit||"Stk.")+'"></label></div><div class="form-row"><label>EK netto<input name="purchase_price_net" type="number" step="0.01" value="'+val("purchase_price_net")+'"></label><label>VK netto<input name="sales_price_net" type="number" step="0.01" value="'+val("sales_price_net")+'"></label></div><div class="form-row"><label>Mindestbestand<input name="minimum_stock" type="number" step="1" min="0" value="'+esc(item?.minimum_stock??0)+'"></label><label>Sollbestand<input name="target_stock" type="number" step="1" min="0" value="'+val("target_stock")+'"></label></div><label>Notiz<textarea name="notes" rows="3">'+val("notes")+'</textarea></label><div class="form-actions"><button type="button" class="secondary" id="inventoryCancel">Abbrechen</button><button class="primary">'+(item?"Änderungen speichern":"Artikel anlegen")+'</button></div></form></div>');
 const form=$("inventoryItemForm"),manufacturer=form.elements.manufacturer,ek=form.elements.purchase_price_net,vk=form.elements.sales_price_net;
 const suggestPrices=()=>{const isOekofen=["ökofen","oekofen"].includes(String(manufacturer.value||"").trim().toLowerCase());ek.placeholder=isOekofen&&vk.value!==""&&ek.value===""?"automatisch: "+(Number(vk.value)*0.68).toFixed(2)+" €":"";vk.placeholder=!isOekofen&&ek.value!==""&&vk.value===""?"automatisch: "+(Number(ek.value)*1.40).toFixed(2)+" €":""};
 manufacturer.addEventListener("input",suggestPrices);vk.addEventListener("input",suggestPrices);ek.addEventListener("input",suggestPrices);suggestPrices();
 $("inventoryCancel").onclick=close;$("inventoryItemForm").onsubmit=async e=>{e.preventDefault();const d=Object.fromEntries(new FormData(e.currentTarget).entries());["purchase_price_net","sales_price_net","minimum_stock","target_stock"].forEach(k=>d[k]=d[k]===""?null:Number(d[k]));if(d.minimum_stock===null)d.minimum_stock=0;Object.keys(d).forEach(k=>{if(d[k]==="")d[k]=null});if(item){d.sevdesk_object_id=item.sevdesk_object_id||null;d.active=true}const r=await fetch(item?"/api/inventory/items/"+item.id:"/api/inventory/items",{method:item?"PUT":"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(d)});if(r.ok){close();load()}else{const x=await r.json().catch(()=>({}));alert(x.detail||"Artikel konnte nicht gespeichert werden.")}};
}
$("newInventoryItem")?.addEventListener("click",()=>itemForm());

$("inventoryDialog")?.addEventListener("click",e=>{if(e.target===$("inventoryDialog"))close()});
activate();
})();