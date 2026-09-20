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
  "max_cell_temperature":PointSpec("°C","STATE","DIAGNOSTIC"),"min_cell_temperature":PointSpec("°C","STATE","DIAGNOSTIC"),
  "max_charge_voltage":PointSpec("V","STATE","DIAGNOSTIC"),"max_charge_current":PointSpec("A","STATE","DIAGNOSTIC"),
  "max_discharge_current":PointSpec("A","STATE","DIAGNOSTIC"),"installed_capacity_ah":PointSpec("Ah","STATE","DIAGNOSTIC"),
  "modules_online":PointSpec("modules","STATE","DIAGNOSTIC"),"modules_offline":PointSpec("modules","STATE","DIAGNOSTIC"),
  "modules_blocking_charge":PointSpec("modules","STATE","DIAGNOSTIC"),"modules_blocking_discharge":PointSpec("modules","STATE","DIAGNOSTIC"),
  "alarm_internal":PointSpec(None,"STATE","DIAGNOSTIC"),"alarm_cell_imbalance":PointSpec(None,"STATE","DIAGNOSTIC"),
  "alarm_high_charge_current":PointSpec(None,"STATE","DIAGNOSTIC"),"alarm_high_discharge_current":PointSpec(None,"STATE","DIAGNOSTIC"),
  "alarm_high_charge_temperature":PointSpec(None,"STATE","DIAGNOSTIC"),"alarm_low_charge_temperature":PointSpec(None,"STATE","DIAGNOSTIC"),
  "alarm_high_temperature":PointSpec(None,"STATE","DIAGNOSTIC"),"alarm_low_temperature":PointSpec(None,"STATE","DIAGNOSTIC"),
  "alarm_high_cell_voltage":PointSpec(None,"STATE","DIAGNOSTIC"),"alarm_low_voltage":PointSpec(None,"STATE","DIAGNOSTIC")},
 "BUFFER":{"temperature_top":PointSpec("°C","SLOW","OPTIMIZATION"),"temperature_upper":PointSpec("°C","SLOW","OPTIMIZATION"),"temperature_middle":PointSpec("°C","SLOW","OPTIMIZATION"),"temperature_lower":PointSpec("°C","SLOW","OPTIMIZATION"),"temperature_bottom":PointSpec("°C","SLOW","OPTIMIZATION"),"volume":PointSpec("L","STATE","DIAGNOSTIC")},
 "COMBINED_STORAGE":{"temperature_top":PointSpec("°C","SLOW","OPTIMIZATION"),"temperature_upper":PointSpec("°C","SLOW","OPTIMIZATION"),"temperature_middle":PointSpec("°C","SLOW","OPTIMIZATION"),"temperature_lower":PointSpec("°C","SLOW","OPTIMIZATION"),"temperature_bottom":PointSpec("°C","SLOW","OPTIMIZATION"),"dhw_temperature":PointSpec("°C","SLOW","OPTIMIZATION"),"volume":PointSpec("L","STATE","DIAGNOSTIC")},
 "DHW":{"temperature":PointSpec("°C","SLOW","OPTIMIZATION"),"temperature_top":PointSpec("°C","SLOW","OPTIMIZATION"),"temperature_bottom":PointSpec("°C","SLOW","OPTIMIZATION"),"target_temperature":PointSpec("°C","STATE","OPTIMIZATION"),"volume":PointSpec("L","STATE","DIAGNOSTIC")},
 "HEATING_CIRCUIT":{"flow_temperature":PointSpec("°C","NORMAL","OPTIMIZATION"),"target_flow_temperature":PointSpec("°C","STATE","OPTIMIZATION"),"return_temperature":PointSpec("°C","NORMAL","MONITORING"),"pump_state":PointSpec(None,"STATE","MONITORING"),"mixer_position":PointSpec("%","NORMAL","MONITORING"),"room_temperature":PointSpec("°C","SLOW","MONITORING"),"room_target_temperature":PointSpec("°C","STATE","OPTIMIZATION")},
 "POWER_TO_HEAT":{"electrical_power":PointSpec("W","FAST","OPTIMIZATION"),"temperature":PointSpec("°C","SLOW"),"target_temperature":PointSpec("°C","STATE")},
 "HEAT_PUMP":{"electrical_power":PointSpec("W","FAST","OPTIMIZATION"),"thermal_power":PointSpec("W","FAST","OPTIMIZATION"),"flow_temperature":PointSpec("°C","NORMAL","OPTIMIZATION"),"return_temperature":PointSpec("°C","NORMAL","MONITORING"),"source_temperature":PointSpec("°C","NORMAL","MONITORING"),"compressor_state":PointSpec(None,"STATE","MONITORING"),"compressor_starts":PointSpec(None,"STATE","DIAGNOSTIC"),"runtime":PointSpec("h","STATE","DIAGNOSTIC"),"state":PointSpec(None,"STATE","OPTIMIZATION")},
 "PELLET_BOILER":{"current_power":PointSpec("W","FAST","OPTIMIZATION"),"boiler_temperature":PointSpec("°C","NORMAL","OPTIMIZATION"),"flame_temperature":PointSpec("°C","NORMAL","MONITORING"),"flow_temperature":PointSpec("°C","NORMAL","OPTIMIZATION"),"return_temperature":PointSpec("°C","NORMAL","MONITORING"),"fuel_consumption_total":PointSpec("kg","STATE","DIAGNOSTIC"),"burner_starts":PointSpec(None,"STATE","DIAGNOSTIC"),"runtime":PointSpec("h","STATE","DIAGNOSTIC"),"state":PointSpec(None,"STATE","OPTIMIZATION")},
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
