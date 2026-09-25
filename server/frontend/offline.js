const INS_OFFLINE_DB="ins-ei-offline-v1";
const INS_OFFLINE_VERSION=2;
const INS_STORES=["customers","customerDetails","visits","maintenances","maintenanceDue","maintenanceDetails"];

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
window.INSOffline={putMany:insOfflinePutMany,put:insOfflinePut,get:insOfflineGet,getAll:insOfflineGetAll};
