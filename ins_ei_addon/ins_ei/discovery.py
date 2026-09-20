"""Read-only Home Assistant entity discovery for mapping assistance."""
from dataclasses import dataclass
from typing import Any

@dataclass(slots=True)
class DiscoveryCandidate:
    entity_id:str;state:Any;name:str|None;unit:str|None;device_class:str|None;state_class:str|None;suggested_domain:str|None;score:int;mapped:bool=False

KEYWORDS={
 "GRID":("grid","netz","em540","meter"),"PV":("pv","solar","photovolta","wechselrichter","inverter"),
 "BATTERY":("pylontech","ess battery","batteriespeicher"),"POWER_TO_HEAT":("ac_thor","ac thor","ohmpilot","heizstab","power to heat"),
 "BUFFER":("buffer","puffer"),"DHW":("hot_water","warmwasser","boiler","dhw"),"HEAT_PUMP":("heat_pump","heat pump","wärmepumpe","waermepumpe","knv"),
 "PELLET_BOILER":("pellematic","ökofen","oekofen","pellet"),"ROOM":("raumtemperatur","humidity","feuchte"),}

EXCLUDE_BATTERY=("iphone","ipad","watch","rauchmelder","smoke","remote","phone")

def discover(states:list[dict[str,Any]],mapped_entities:set[str]|None=None)->list[DiscoveryCandidate]:
    mapped_entities=mapped_entities or set();out=[]
    for item in states:
        entity_id=item.get("entity_id","");attrs=item.get("attributes") or {};name=attrs.get("friendly_name");hay=f"{entity_id} {name or ''}".lower()
        best_domain=None;best_score=0
        for domain,words in KEYWORDS.items():
            score=sum(2 for w in words if w in hay)
            if domain=="BATTERY" and any(x in hay for x in EXCLUDE_BATTERY):score=0
            if score>best_score:best_domain,best_score=domain,score
        unit=attrs.get("unit_of_measurement");dc=attrs.get("device_class")
        if best_domain and dc in ("power","energy","voltage","current","temperature","humidity","battery"):best_score+=1
        if best_score:out.append(DiscoveryCandidate(entity_id,item.get("state"),name,unit,dc,attrs.get("state_class"),best_domain,best_score,entity_id in mapped_entities))
    return sorted(out,key=lambda x:(x.mapped,-x.score,x.entity_id))
