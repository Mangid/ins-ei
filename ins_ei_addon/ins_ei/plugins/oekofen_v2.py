"""OekoFEN Plugin V2 reference implementation.

Owns manufacturer protocol, normalized inputs, writable actuators and health.
The optimizer/core never sees pe1/ww1 transport details.
"""
from __future__ import annotations
from datetime import datetime,timezone
from typing import Any
from .oekofen import OekoFENPlugin as LegacyOekoFEN
from ..plugin_sdk import PluginManifest,PluginKind,InputDescriptor,ActuatorDescriptor,PluginHealth

MANIFEST=PluginManifest(
    plugin_id="oekofen",name="ÖkoFEN",version="2.0.0",kind=PluginKind.DEVICE,
    description="Pellematic/Pellematic Compact heating controller",multi_instance=True,
)

class OekoFENInstance:
    def __init__(self,instance_id:str,config:dict):
        self.instance_id=instance_id;self.config=dict(config)
        self.driver=LegacyOekoFEN(config["host"],config.get("password",""),int(config.get("port",4321)))
        self._last_success=None;self._last_error=None
    def manifest(self):return MANIFEST
    def inputs(self):
        return [
            InputDescriptor("weather.outdoor_temperature","Außentemperatur","°C"),
            InputDescriptor("buffer.temperature_upper","Puffer oben","°C"),
            InputDescriptor("buffer.temperature_lower","Puffer unten","°C"),
            InputDescriptor("dhw.temperature","Warmwasser","°C"),
            InputDescriptor("dhw.target_temperature","WW Soll","°C"),
            InputDescriptor("pellet_boiler.boiler_temperature","Kesseltemperatur","°C"),
            InputDescriptor("pellet_boiler.flame_temperature","Flammtemperatur","°C"),
            InputDescriptor("pellet_boiler.modulation","Modulation","%"),
            InputDescriptor("pellet_boiler.state","Kesselstatus"),
            InputDescriptor("hk1.flow_temperature","HK1 Vorlauf","°C"),
            InputDescriptor("hk2.flow_temperature","HK2 Vorlauf","°C"),
        ]
    def actuators(self):
        return [
            ActuatorDescriptor("boiler.mode","Kessel Betriebsart","thermal.boiler_mode",
                               "Normalisierte Betriebsart AUTO/OFF",True),
            ActuatorDescriptor("dhw.once","WW Einmalladung","thermal.dhw_once",
                               "Einmalige Warmwasserladung",True),
        ]
    def _probe(self):
        try:
            p=self.driver.read();self._last_success=datetime.now(timezone.utc).isoformat();self._last_error=None;return p
        except Exception as exc:self._last_error=str(exc);raise
    def read_points(self):
        probe=self._probe();return {f"{p.component_id}.{p.point}":p.value for p in probe.points}
    def _actuator_values(self):return self.driver.actuator_values()
    def read_actuator(self,actuator_id):
        raw=self._actuator_values()
        if actuator_id=="boiler.mode":
            value=raw.get("oekofen.pe1.mode")
            return "AUTO" if str(value) in ("1","1.0","auto","AUTO") else "OFF"
        if actuator_id=="dhw.once":
            return str(raw.get("oekofen.ww1.heat_once")).lower() in ("true","1","on")
        raise KeyError(actuator_id)
    def write_actuator(self,actuator_id,value):
        if actuator_id=="boiler.mode":
            mode=str(value).upper()
            if mode not in ("AUTO","OFF"):raise ValueError("OEKOFEN_MODE_INVALID")
            self.driver.set_boiler_mode(1 if mode=="AUTO" else 0);return
        if actuator_id=="dhw.once":self.driver.set_dhw_once(bool(value));return
        raise KeyError(actuator_id)
    def verify_actuator(self,actuator_id,value):
        expected=str(value).upper() if actuator_id=="boiler.mode" else bool(value)
        actual=self.read_actuator(actuator_id)
        return actual==expected
    def health(self):
        return PluginHealth(self._last_error is None,self._last_error or "OK",self._last_success,
                            {"instance_id":self.instance_id,"host":self.config.get("host"),"port":self.config.get("port",4321)})

def factory(instance_id:str,config:dict):return OekoFENInstance(instance_id,config)
