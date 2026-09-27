"""Manufacturer-neutral baseline, ownership and restore."""
from datetime import datetime,timezone
import json
class ActuatorManager:
    def __init__(self,plugins,state_path):self.plugins=plugins;self.path=state_path;self.state=self._load()
    def _load(self):
        try:return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:return {"version":1,"baseline":{},"owned":{},"sealed":False}
    def _save(self):self.path.write_text(json.dumps(self.state,ensure_ascii=False,indent=2),encoding="utf-8")
    def key(self,iid,aid):return f"{iid}:{aid}"
    def commission(self):
        if self.state.get("owned"):raise RuntimeError("ACTUATORS_CURRENTLY_OWNED")
        base={}
        for iid,p in self.plugins.instances().items():
            for a in p.actuators():
                if a.restorable:base[self.key(iid,a.actuator_id)]={"value":p.read_actuator(a.actuator_id),"captured_at":datetime.now(timezone.utc).isoformat()}
        self.state={"version":1,"baseline":base,"owned":{},"sealed":True};self._save()
    def apply(self,iid,aid,value,owner):
        if not self.state.get("sealed"):raise RuntimeError("BASELINE_NOT_COMMISSIONED")
        key=self.key(iid,aid)
        if key not in self.state["baseline"]:raise RuntimeError(f"BASELINE_MISSING:{key}")
        p=self.plugins.get(iid);p.write_actuator(aid,value)
        if not p.verify_actuator(aid,value):raise RuntimeError(f"VERIFY_FAILED:{key}")
        self.state["owned"][key]={"owner":owner,"since":datetime.now(timezone.utc).isoformat()};self._save()
    def restore_all(self):
        failed={}
        for key in list(self.state["owned"]):
            iid,aid=key.split(":",1);p=self.plugins.get(iid);value=self.state["baseline"][key]["value"]
            try:
                p.write_actuator(aid,value)
                if not p.verify_actuator(aid,value):raise RuntimeError("VERIFY_FAILED")
                self.state["owned"].pop(key,None)
            except Exception as e:failed[key]=str(e)
        self._save();return failed
