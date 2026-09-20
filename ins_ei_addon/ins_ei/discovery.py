"""Read-only Home Assistant entity discovery for mapping assistance."""
from dataclasses import dataclass
from typing import Any

@dataclass(slots=True)
class DiscoveryCandidate:
    entity_id:str
    state:Any
    name:str|None
    unit:str|None
    device_class:str|None
    state_class:str|None
    suggested_domain:str|None
    score:int

KEYWORDS={
 "GRID":("grid","netz","em540","meter"),
 "PV":("pv","solar","photovolta","wechselrichter","inverter"),
 "BATTERY":("battery","batter","pylontech","akku","soc"),
 "POWER_TO_HEAT":("ac_thor","ac thor","ohmpilot","heizstab","power to heat"),
 "BUFFER":("buffer","puffer"),
 "DHW_STORAGE":("hot_water","warmwasser","boiler","dhw"),
 "HEAT_PUMP":("heat_pump","heat pump","wärmepumpe","waermepumpe","knv"),
 "PELLET_BOILER":("pellematic","ökofen","oekofen","pellet"),
 "ROOM":("temperature","humidity","feuchte","room"),
}

def discover(states:list[dict[str,Any]])->list[DiscoveryCandidate]:
    out=[]
    for item in states:
        entity_id=item.get("entity_id","")
        attrs=item.get("attributes") or {}
        name=attrs.get("friendly_name")
        hay=f"{entity_id} {name or ''}".lower()
        best_domain=None; best_score=0
        for domain,words in KEYWORDS.items():
            score=sum(2 for w in words if w in hay)
            if score>best_score: best_domain,best_score=domain,score
        unit=attrs.get("unit_of_measurement"); dc=attrs.get("device_class")
        if dc in ("power","energy","voltage","current","temperature","humidity","battery"): best_score+=1
        if best_score:
            out.append(DiscoveryCandidate(entity_id,item.get("state"),name,unit,dc,attrs.get("state_class"),best_domain,best_score))
    return sorted(out,key=lambda x:(-x.score,x.entity_id))
