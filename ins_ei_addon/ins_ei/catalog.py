"""Central INS-EI point catalog.

One source of truth for mapping validation, default freshness, discovery hints
and future UI selectors.
"""
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class PointSpec:
    unit: str | None
    freshness: str
    role: str = "MONITORING"

CATALOG={
 "GRID":{
  "power":PointSpec("W","FAST","OPTIMIZATION"),"import_energy":PointSpec("kWh","STATE"),"export_energy":PointSpec("kWh","STATE"),
  "voltage_l1":PointSpec("V","NORMAL","DIAGNOSTIC"),"voltage_l2":PointSpec("V","NORMAL","DIAGNOSTIC"),"voltage_l3":PointSpec("V","NORMAL","DIAGNOSTIC"),
  "current_l1":PointSpec("A","FAST","DIAGNOSTIC"),"current_l2":PointSpec("A","FAST","DIAGNOSTIC"),"current_l3":PointSpec("A","FAST","DIAGNOSTIC"),"frequency":PointSpec("Hz","NORMAL","DIAGNOSTIC")},
 "PV":{"power":PointSpec("W","FAST","OPTIMIZATION"),"energy_today":PointSpec("kWh","STATE"),"energy_total":PointSpec("kWh","STATE")},
 "BATTERY":{
  "soc":PointSpec("%","STATE","OPTIMIZATION"),"power":PointSpec("W","FAST","OPTIMIZATION"),"voltage":PointSpec("V","NORMAL","DIAGNOSTIC"),
  "current":PointSpec("A","FAST","DIAGNOSTIC"),"temperature":PointSpec("°C","SLOW","DIAGNOSTIC"),"soh":PointSpec("%","STATE","DIAGNOSTIC"),
  "max_cell_voltage":PointSpec("V","NORMAL","DIAGNOSTIC"),"min_cell_voltage":PointSpec("V","NORMAL","DIAGNOSTIC"),
  "max_cell_temperature":PointSpec("°C","SLOW","DIAGNOSTIC"),"min_cell_temperature":PointSpec("°C","SLOW","DIAGNOSTIC")},
 "BUFFER":{"temperature_top":PointSpec("°C","SLOW","OPTIMIZATION"),"temperature_upper":PointSpec("°C","SLOW","OPTIMIZATION"),"temperature_middle":PointSpec("°C","SLOW","OPTIMIZATION"),"temperature_lower":PointSpec("°C","SLOW","OPTIMIZATION"),"temperature_bottom":PointSpec("°C","SLOW","OPTIMIZATION")},
 "DHW":{"temperature":PointSpec("°C","SLOW","OPTIMIZATION"),"temperature_top":PointSpec("°C","SLOW","OPTIMIZATION"),"temperature_bottom":PointSpec("°C","SLOW","OPTIMIZATION")},
 "POWER_TO_HEAT":{"electrical_power":PointSpec("W","FAST","OPTIMIZATION"),"temperature":PointSpec("°C","SLOW"),"target_temperature":PointSpec("°C","STATE")},
 "HEAT_PUMP":{"electrical_power":PointSpec("W","FAST","OPTIMIZATION"),"thermal_power":PointSpec("W","FAST","OPTIMIZATION"),"flow_temperature":PointSpec("°C","NORMAL"),"return_temperature":PointSpec("°C","NORMAL"),"state":PointSpec(None,"STATE")},
 "PELLET_BOILER":{"current_power":PointSpec("W","FAST","OPTIMIZATION"),"flow_temperature":PointSpec("°C","NORMAL"),"return_temperature":PointSpec("°C","NORMAL"),"fuel_consumption_total":PointSpec("kg","STATE"),"state":PointSpec(None,"STATE")},
 "ROOM":{"temperature":PointSpec("°C","SLOW"),"humidity":PointSpec("%","SLOW"),"co2":PointSpec("ppm","NORMAL"),"heating_target":PointSpec("°C","STATE"),"valve_position":PointSpec("%","NORMAL")},
 "LOAD":{"power":PointSpec("W","FAST"),"energy_today":PointSpec("kWh","STATE"),"energy_total":PointSpec("kWh","STATE"),"state":PointSpec(None,"STATE")}
}

ALIASES={"DHW_STORAGE":"DHW"}

def component_type(component_id:str)->str:
    raw=component_id.upper()
    base=raw.split(":",1)[0].split("_",1)[0]
    if raw in CATALOG:return raw
    if base in CATALOG:return base
    return ALIASES.get(raw,ALIASES.get(base,raw))

def point_spec(component_id:str,point:str)->PointSpec|None:
    return CATALOG.get(component_type(component_id),{}).get(point)

def component_choices()->list[str]: return sorted(CATALOG)
def point_choices(component:str)->list[str]: return sorted(CATALOG.get(component_type(component),{}))
