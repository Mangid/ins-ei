"""Read-only Home Assistant discovery with thermal mapping suggestions."""
from dataclasses import dataclass
from typing import Any

@dataclass(slots=True)
class DiscoveryCandidate:
    entity_id:str; state:Any; name:str|None; unit:str|None; device_class:str|None
    state_class:str|None; suggested_domain:str|None; suggested_point:str|None
    score:int; mapped_to:str|None=None; source_kind:str="MEASUREMENT"

KEYWORDS={
 "WEATHER":("aussentemperatur","außentemperatur","aussensensor","außensensor","outdoor temperature","outside temperature"),
 "GRID":("grid","netz","em540","meter"),
 "PV":("pv","solar","photovolta","wechselrichter","inverter"),
 "BATTERY":("pylontech","ess battery","batteriespeicher"),
 "POWER_TO_HEAT":("ac_thor","ac thor","ohmpilot","heizstab","power to heat"),
 "BUFFER":("buffer_storage","buffer storage","puffer"),
 "DHW":("hot_water","hot water","warmwasser","boiler","dhw"),
 "HEAT_PUMP":("heat_pump","heat pump","wärmepumpe","waermepumpe","knv"),
 "PELLET_BOILER":("pellematic","ökofen","oekofen","pellet"),
 "HEATING_CIRCUIT":("heizkreis","heating circuit","hk1","hk2"),
 "ROOM":("raumtemperatur","humidity","feuchte"),
 "MARKET":("epex","awattar","spot price","market price","marktpreis"),
 "FORECAST":("victron_remote_monitoring","forecast","prognose","geschatzte_energieerzeugung","geschätzte energieerzeugung"),
}
EXCLUDE_BATTERY=("iphone","ipad","watch","rauchmelder","smoke","remote","phone")

def contains(hay,*terms): return any(term in hay for term in terms)

def source_kind(entity,hay):
    domain=entity.split(".",1)[0]
    parameter_words=("abschalt","einschalttemperatur","wassertemp_min","soll","setpoint","grenze","limit","min_","max_","mintemp","parameter")
    status_words=("betriebsart","status","state","alarm","fehler","störung","stoerung")
    if domain in ("number","input_number","select","input_select"):
        return "PARAMETER"
    if any(word in hay for word in status_words):
        return "STATUS"
    if any(word in hay for word in parameter_words):
        return "PARAMETER"
    return "MEASUREMENT"

def suggest(domain,hay,dc,kind):
    if domain=="WEATHER" and dc=="temperature":return "outdoor_temperature"
    if domain=="MARKET":
        if contains(hay,"epex","spot","market_price","marktpreis"):return "spot_price"
    if domain=="FORECAST":
        if contains(hay,"verbrauch","consumption"):
            if contains(hay,"aktuelle_stunde","current_hour"):return "consumption_current_hour"
            if contains(hay,"morgen","tomorrow"):return "consumption_tomorrow"
            if contains(hay,"heute","today"):return "consumption_today"
        if contains(hay,"energieerzeugung","pv","solar","generation"):
            if contains(hay,"aktuelle_stunde","current_hour"):return "pv_current_hour"
            if contains(hay,"morgen","tomorrow"):return "pv_tomorrow"
            if contains(hay,"heute","today"):return "pv_today"
    if domain=="BATTERY":
        if contains(hay,"state_of_health","state of health"):return "soh"
        if contains(hay,"ladestand"," soc") or dc=="battery":return "soc"
        if "maximale zellspannung" in hay:return "max_cell_voltage"
        if "minimale zellspannung" in hay:return "min_cell_voltage"
        if "maximale zelltemperatur" in hay:return "max_cell_temperature"
        if "minimale zelltemperatur" in hay:return "min_cell_temperature"
        if dc=="power" or "leistung" in hay:return "power"
        if dc=="voltage" or "spannung" in hay:return "voltage"
        if dc=="current" or "stromst" in hay:return "current"
        if dc=="temperature" or "temperatur" in hay:return "temperature"
    if domain in ("GRID","PV") and dc=="power":return "power"
    if domain=="POWER_TO_HEAT":
        if dc=="power" or "leistung" in hay:return "electrical_power"
        if contains(hay,"solltemperatur","target temperature"):return "target_temperature"
        if dc=="temperature":return "temperature"
    if domain=="BUFFER" and dc=="temperature" and kind=="MEASUREMENT":
        if contains(hay,"ganz oben","top"):return "temperature_top"
        if contains(hay,"einschalt","tpo","oben","upper"):return "temperature_upper"
        if contains(hay,"mitte","middle"):return "temperature_middle"
        if contains(hay,"ausschalt","tpm","unten","lower"):return "temperature_lower"
    if domain=="DHW":
        if kind=="PARAMETER":
            if contains(hay,"max_set","maximum","maximal"):return None
            if contains(hay,"temp_set","wassertemp_soll","target temperature"):return "target_temperature"
            return None
        if contains(hay,"aus_temperatur","ein_temperatur") and dc=="temperature":return "temperature"
        if dc=="temperature":return "temperature"
    if domain=="PELLET_BOILER":
        if kind=="PARAMETER":return None
        if contains(hay,"flammraum","flame"):return "flame_temperature"
        if contains(hay,"kesseltemperatur","boiler temperature"):return "boiler_temperature"
        if contains(hay,"vorlauf","flow temperature"):return "flow_temperature"
        if contains(hay,"rücklauf","rucklauf","return temperature"):return "return_temperature"
        if contains(hay,"verbrauch","fuel consumption"):
            if contains(hay,"gestern","yesterday"):return "fuel_consumption_yesterday"
            if contains(hay,"heute","today"):return "fuel_consumption_today"
            if contains(hay,"gesamt","total"):return "fuel_consumption_total"
            return None
        if contains(hay,"starts","starts"):return "burner_starts"
        if contains(hay,"laufzeit","runtime"):return "runtime"
        if contains(hay,"betriebsart","status","state"):return "state"
    if domain=="HEAT_PUMP":
        if kind=="PARAMETER":return None
        if dc=="power":return "electrical_power"
        if contains(hay,"vorlauf","flow"):return "flow_temperature"
        if contains(hay,"rücklauf","rucklauf","return"):return "return_temperature"
        if contains(hay,"quelle","source"):return "source_temperature"
        if contains(hay,"verdichter","compressor") and contains(hay,"start"):return "compressor_starts"
        if contains(hay,"verdichter","compressor"):return "compressor_state"
        if contains(hay,"laufzeit","runtime"):return "runtime"
        if contains(hay,"status","state"):return "state"
    if domain=="HEATING_CIRCUIT":
        if kind=="PARAMETER" and contains(hay,"soll","target") and contains(hay,"vorlauf","flow"):return "target_flow_temperature"
        if contains(hay,"soll","target") and contains(hay,"vorlauf","flow"):return "target_flow_temperature"
        if contains(hay,"vorlauf","flow"):return "flow_temperature"
        if contains(hay,"rücklauf","rucklauf","return"):return "return_temperature"
        if contains(hay,"mischer","mixer"):return "mixer_position"
        if contains(hay,"pumpe","pump"):return "pump_state"
    if domain=="ROOM":
        if dc=="temperature":return "temperature"
        if dc=="humidity":return "humidity"
    return None

def discover(states:list[dict[str,Any]],mapped:dict[str,str]|None=None)->list[DiscoveryCandidate]:
    mapped=mapped or {};out=[]
    for item in states:
        entity=item.get("entity_id","");attrs=item.get("attributes") or {};name=attrs.get("friendly_name");hay=f"{entity} {name or ''}".lower()
        best=None;score=0
        for domain,words in KEYWORDS.items():
            value=sum(2 for word in words if word in hay)
            if domain=="BATTERY" and any(word in hay for word in EXCLUDE_BATTERY):value=0
            if value>score:best,score=domain,value
        dc=attrs.get("device_class");unit=attrs.get("unit_of_measurement")
        if best and dc in ("power","energy","voltage","current","temperature","humidity","battery"):score+=1
        if score:
            kind=source_kind(entity,hay)
            point=suggest(best,hay,dc,kind)
            out.append(DiscoveryCandidate(entity,item.get("state"),name,unit,dc,attrs.get("state_class"),best,point,score,mapped.get(entity),kind))
    return sorted(out,key=lambda x:(x.mapped_to is not None,x.suggested_point is None,-x.score,x.entity_id))
