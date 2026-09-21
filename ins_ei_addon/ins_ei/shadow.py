"""Read-only SHADOW decision layer for INS-EI pilot."""
from dataclasses import dataclass,asdict
from datetime import datetime,timezone
from typing import Any
from ins_ei.model import DataQuality

@dataclass(slots=True)
class ShadowDecision:
    action:str
    reason:str
    confidence:str
    inputs:dict[str,Any]
    timestamp:str
    def to_dict(self):return asdict(self)

def _point(site,kind,name):
    for component in site.components_by_kind(kind):
        point=component.point(name)
        if point:return point
    return None

def _usable(point):
    return point is not None and point.quality==DataQuality.GOOD and point.value is not None

def evaluate(site):
    pv=_point(site,"PV","power");grid=_point(site,"GRID","power");soc=_point(site,"BATTERY","soc")
    pth=_point(site,"POWER_TO_HEAT","electrical_power");buffer=_point(site,"BUFFER","temperature_upper");dhw=_point(site,"DHW","temperature")
    inputs={}
    for key,point in (("pv_power_w",pv),("grid_power_w",grid),("battery_soc_pct",soc),("power_to_heat_w",pth),("buffer_upper_c",buffer),("dhw_c",dhw)):
        inputs[key]={"value":point.value if point else None,"quality":point.quality.value if point else "MISSING"}
    now=datetime.now(timezone.utc).isoformat()
    critical=[("grid",grid),("pv",pv),("battery_soc",soc)]
    missing=[name for name,point in critical if not _usable(point)]
    if missing:
        return ShadowDecision("OBSERVE_ONLY","Keine Optimierungsentscheidung: kritische Eingangsdaten fehlen oder sind nicht GOOD: "+", ".join(missing),"LOW",inputs,now)
    pv_w=float(pv.value);grid_w=float(grid.value);soc_pct=float(soc.value)
    if grid_w>100:
        action="BATTERY_SUPPORT_LOAD" if soc_pct>20 else "GRID_IMPORT"
        reason=f"Netzbezug {grid_w:.0f} W bei Batterie-SOC {soc_pct:.1f} %. "+("Batterie könnte im SHADOW-Modell den Bezug reduzieren." if action=="BATTERY_SUPPORT_LOAD" else "SOC-Schutz hat Vorrang.")
    elif grid_w<-100:
        surplus=-grid_w
        if soc_pct<95:
            action="CHARGE_BATTERY";reason=f"PV-Überschuss ca. {surplus:.0f} W und Batterie-SOC {soc_pct:.1f} %. Batterie laden wäre die erste Option."
        elif _usable(pth) and _usable(buffer):
            action="POWER_TO_HEAT";reason=f"PV-Überschuss ca. {surplus:.0f} W bei hohem Batterie-SOC {soc_pct:.1f} %. Power-to-Heat ist verfügbar und kann thermische Energie aufnehmen."
        else:
            action="EXPORT_PV";reason=f"PV-Überschuss ca. {surplus:.0f} W bei Batterie-SOC {soc_pct:.1f} %. Keine belastbare Power-to-Heat-Freigabe; Einspeisung bleibt die sichere SHADOW-Annahme."
    else:
        action="BALANCED";reason=f"Netzleistung {grid_w:.0f} W liegt nahe dem ausgeglichenen Betrieb."
    return ShadowDecision(action,reason,"MEDIUM",inputs,now)
