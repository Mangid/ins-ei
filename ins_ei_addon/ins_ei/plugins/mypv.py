"""my-PV normalization for local AC-THOR family telemetry."""
from __future__ import annotations
from .base import DeviceIdentity,PluginPoint,PluginProbe
from .transports import MyPVTransport

class MyPVPlugin:
    plugin_id="mypv";manufacturer="my-PV"
    def __init__(self,host,port=502,unit_id=1,http_enabled=True):
        self.transport=MyPVTransport(host,port,unit_id,http_enabled=http_enabled)
    def detect(self):
        d=self.transport.read();http=d.get("http") or {};device=str(http.get("device") or "")
        model="AC-THOR 9s" if d.get("state_9s") is not None else (device or "AC-THOR family")
        caps={"power_to_heat"}
        if d.get("state_9s") is not None:caps|={"three_phase","0_9kw"}
        return DeviceIdentity("my-PV","AC-THOR",model=model,firmware=str(http.get("fwversion")) if http.get("fwversion") is not None else None,profile="ac_thor_9s" if "9s" in model else "generic",capabilities=caps,raw={"device":device})
    def read(self):
        d=self.transport.read();http=d.get("http") or {};points=[];used={"device_power_w","operation_state","state_9s","http"}
        def add(key,point,unit=None):
            v=d.get(key)
            if v is not None:points.append(PluginPoint("power_to_heat",point,v,unit,key))
        add("device_power_w","electrical_power","W")
        if d.get("operation_state") is not None:points.append(PluginPoint("power_to_heat","state",d["operation_state"],None,"operation_state"))
        # Keep all HTTP values and diagnostic registers available for future profiles.
        unmapped={k:v for k,v in d.items() if k not in used and v is not None}
        unmapped.update({f"http.{k}":v for k,v in http.items()})
        return PluginProbe(self.detect(),points,unmapped)
