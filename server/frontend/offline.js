const INS_OFFLINE_DB="ins-ei-offline-v1";
const INS_OFFLINE_VERSION=4;
const INS_STORES=["customers","customerDetails","visits","maintenances","maintenanceDue","maintenanceDetails","syncQueue","fileQueue"];

function insDb(){
  return new Promise((resolve,reject)=>{
    const req=indexedDB.open(INS_OFFLINE_DB,INS_OFFLINE_VERSION);
    req.onupgradeneeded=()=>{
      const db=req.result;
      INS_STORES.forEach(name=>{if(!db.objectStoreNames.contains(name))db.createObjectStore(name,{keyPath:"id"})});
    };
    req.onsuccess=()=>resolve(req.result);
    req.onerror=()=>reject(req.error);
  });
}
async function insOfflinePutMany(store,items){
  const db=await insDb();
  await new Promise((resolve,reject)=>{
    const tx=db.transaction(store,"readwrite"),os=tx.objectStore(store);
    items.forEach(item=>os.put(item));
    tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);
  });
  db.close();
}
async function insOfflinePut(store,item){return insOfflinePutMany(store,[item])}
async function insOfflineGet(store,id){
  const db=await insDb();
  const value=await new Promise((resolve,reject)=>{
    const req=db.transaction(store,"readonly").objectStore(store).get(id);
    req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(req.error);
  });
  db.close();return value;
}
async function insOfflineGetAll(store){
  const db=await insDb();
  const values=await new Promise((resolve,reject)=>{
    const req=db.transaction(store,"readonly").objectStore(store).getAll();
    req.onsuccess=()=>resolve(req.result||[]);req.onerror=()=>reject(req.error);
  });
  db.close();return values;
}
async function insOfflineDelete(store,id){
  const db=await insDb();
  await new Promise((resolve,reject)=>{const tx=db.transaction(store,"readwrite");tx.objectStore(store).delete(id);tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error)});
  db.close();
}
async function insUpdateStatus(){
  const el=document.getElementById("syncStatus");if(!el)return;
  let n=0;try{n=(await insOfflineGetAll("syncQueue")).length+(await insOfflineGetAll("fileQueue")).length}catch(e){}
  el.classList.toggle("offline",!navigator.onLine);el.classList.toggle("pending",navigator.onLine&&n>0);el.classList.toggle("synced",navigator.onLine&&n===0);
  el.textContent=!navigator.onLine?(n?`Offline · ${n} offen`:"Offline"):(n?`${n} Änderung${n===1?"":"en"} offen`:"Synchronisiert");
}
async function insQueue(method,url,body){
  const item={id:Date.now()+"-"+Math.random().toString(16).slice(2),method,url,body,created_at:new Date().toISOString()};
  await insOfflinePut("syncQueue",item);await insUpdateStatus();return item;
}
async function insQueueFile(customerId,file,meta={}){
  const data=await new Promise((resolve,reject)=>{
    const reader=new FileReader();
    reader.onload=()=>resolve(reader.result);
    reader.onerror=()=>reject(reader.error);
    reader.readAsDataURL(file);
  });
  const item={id:Date.now()+"-"+Math.random().toString(16).slice(2),customer_id:customerId,name:file.name,type:file.type||"application/octet-stream",data,meta,created_at:new Date().toISOString()};
  await insOfflinePut("fileQueue",item);
  await insUpdateStatus();
  return item;
}

function insQueuedFileBlob(item){
  const parts=item.data.split(",");
  const raw=atob(parts[1]);
  const bytes=new Uint8Array(raw.length);
  for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);
  return new Blob([bytes],{type:item.type});
}
async function insSyncFiles(){
  if(!navigator.onLine)return;
  const items=await insOfflineGetAll("fileQueue");
  for(const item of items){
    try{
      const fd=new FormData();
      fd.append("file",insQueuedFileBlob(item),item.name);
      Object.entries(item.meta||{}).forEach(([key,value])=>{if(value!==null&&value!==undefined&&value!=="")fd.append(key,value)});
      const r=await fetch("/api/v1/customers/"+item.customer_id+"/files",{method:"POST",body:fd});
      if(r.ok)await insOfflineDelete("fileQueue",item.id);
    }catch(e){}
  }
}

async function insSync(){
  if(!navigator.onLine){await insUpdateStatus();return;}
  const items=await insOfflineGetAll("syncQueue");
  for(const item of items){
    try{const r=await fetch(item.url,{method:item.method,headers:{"Content-Type":"application/json"},body:JSON.stringify(item.body)});if(r.ok)await insOfflineDelete("syncQueue",item.id)}catch(e){}
  }
  await insSyncFiles();
  await insUpdateStatus();
}
window.addEventListener("online",()=>insSync());
window.addEventListener("offline",()=>insUpdateStatus());
window.INSOffline={putMany:insOfflinePutMany,put:insOfflinePut,get:insOfflineGet,getAll:insOfflineGetAll,remove:insOfflineDelete,queue:insQueue,queueFile:insQueueFile,sync:insSync,status:insUpdateStatus};
window.addEventListener("DOMContentLoaded",()=>insUpdateStatus());
insSync();
