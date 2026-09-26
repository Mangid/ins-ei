"""OekoFEN normalization from raw /all controller data."""
from __future__ import annotations
from .base import DeviceIdentity,PluginPoint,PluginProbe
from .transports import OekoFENTransport

def _raw(v):
    return v.get("val") if isinstance(v,dict) else v
def _num(v,f=1.0):
    try:return float(_raw(v))*f
    except (TypeError,ValueError):return None
def _text(v):
    v=_raw(v);return None if v is None else str(v)

class OekoFENPlugin:
    plugin_id="oekofen";manufacturer="OekoFEN"
    def __init__(self,host,password,port=4321):
        self.transport=OekoFENTransport(host,password,port)
        self._last_data=None
        self._last_read=0.0
    def _data(self):
        import time
        now=time.monotonic()
        if self._last_data is None or now-self._last_read>=2.6:
            self._last_data=self.transport.read_all();self._last_read=now
        return self._last_data
    def detect(self):
        data=self._data()
        caps={k for k in ("pe1","pu1","ww1","hk1","hk2") if isinstance(data.get(k),dict)}
        return DeviceIdentity("OekoFEN","Pellematic",profile="json_all",capabilities=caps,raw={"sections":sorted(data)})
    def read(self):
        d=self._data();points=[];used=set()
        def add(section,key,cid,point,unit=None,factor=1.0,text=False):
            sec=d.get(section) or {}
            if key not in sec:return
            used.add((section,key));v=_text(sec[key]) if text else _num(sec[key],factor)
            if v is not None:points.append(PluginPoint(cid,point,v,unit,f"{section}.{key}"))
        add("system","L_ambient","weather","outdoor_temperature","°C",.1)
        add("system","L_boiler_temp","pellet_boiler","boiler_temperature","°C",.1)
        add("pu1","L_tpo_act","buffer","temperature_upper","°C",.1)
        add("pu1","L_tpm_act","buffer","temperature_lower","°C",.1)
        add("ww1","L_ontemp_act","dhw","temperature","°C",.1)
        # DHW charging signals for Thermal Shadow monitoring.  Each point is
        # added only when the controller actually exposes the corresponding
        # /all key, so installations with a different WW configuration remain
        # compatible.
        add("ww1","L_temp_set","dhw","target_temperature","°C",.1)
        add("ww1","L_offtemp_act","dhw","temperature_bottom","°C",.1)
        add("ww1","L_pump","dhw","pump_state",None,1,True)
        add("ww1","L_statetext","dhw","state",None,1,True)
        add("pu1","L_pump","buffer","pump_state",None,1,True)
        add("pu1","L_statetext","buffer","state",None,1,True)
        add("pe1","L_temp_act","pellet_boiler","boiler_temperature","°C",.1)
        add("pe1","L_frt_temp_act","pellet_boiler","flame_temperature","°C",.1)
        add("pe1","L_modulation","pellet_boiler","modulation","%",1)
        add("pe1","L_br","pellet_boiler","burner_state",None,1,True)
        add("pe1","L_statetext","pellet_boiler","state",None,1,True)
        for hk in ("hk1","hk2"):
            add(hk,"L_flowtemp_act",hk,"flow_temperature","°C",.1)
            add(hk,"L_flowtemp_set",hk,"target_flow_temperature","°C",.1)
            add(hk,"L_pump",hk,"pump_state",None,1,True)
            add(hk,"L_statetext",hk,"state",None,1,True)
        unmapped={f"{sec}.{key}":_raw(value) for sec,vals in d.items() if isinstance(vals,dict) for key,value in vals.items() if (sec,key) not in used}
        identity=DeviceIdentity("OekoFEN","Pellematic",profile="json_all",capabilities={k for k in ("pe1","pu1","ww1","hk1","hk2") if k in d})
        return PluginProbe(identity,points,unmapped)
