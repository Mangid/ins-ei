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
 "PV":{"power":PointSpec("W","STATE","OPTIMIZATION"),"energy_today":PointSpec("kWh","STATE"),"energy_total":PointSpec("kWh","STATE")},
 "BATTERY":{
  "soc":PointSpec("%","STATE","OPTIMIZATION"),"power":PointSpec("W","FAST","OPTIMIZATION"),"voltage":PointSpec("V","STATE","DIAGNOSTIC"),
  "current":PointSpec("A","FAST","DIAGNOSTIC"),"temperature":PointSpec("°C","STATE","DIAGNOSTIC"),"soh":PointSpec("%","STATE","DIAGNOSTIC"),
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
 "PELLET_BOILER":{"current_power":PointSpec("W","FAST","OPTIMIZATION"),"boiler_temperature":PointSpec("°C","NORMAL","OPTIMIZATION"),"flame_temperature":PointSpec("°C","NORMAL","MONITORING"),"flow_temperature":PointSpec("°C","NORMAL","OPTIMIZATION"),"return_temperature":PointSpec("°C","NORMAL","MONITORING"),"fuel_consumption_total":PointSpec("kg","STATE","DIAGNOSTIC"),"fuel_consumption_today":PointSpec("kg","STATE","DIAGNOSTIC"),"fuel_consumption_yesterday":PointSpec("kg","STATE","DIAGNOSTIC"),"burner_starts":PointSpec(None,"STATE","DIAGNOSTIC"),"runtime":PointSpec("h","STATE","DIAGNOSTIC"),"state":PointSpec(None,"STATE","OPTIMIZATION")},
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


DESCRIPTIONS={
("GRID","power"):"Aktuelle Wirkleistung am Netzanschlusspunkt. Positiv = Netzbezug, negativ = Einspeisung.",
("GRID","import_energy"):"Kumulierte aus dem öffentlichen Netz bezogene elektrische Energie.",
("GRID","export_energy"):"Kumulierte in das öffentliche Netz eingespeiste elektrische Energie.",
("PV","power"):"Aktuelle gesamte elektrische Leistung der Photovoltaikanlage.",
("PV","energy_today"):"Heute erzeugte PV-Energie.",
("PV","energy_total"):"Gesamterzeugung der Photovoltaikanlage als fortlaufender Energiezähler.",
("BATTERY","soc"):"Ladezustand des gesamten Batteriesystems in Prozent. Zentral für SOC-Schutz und Lade-/Entladeplanung.",
("BATTERY","power"):"Aktuelle Batterieleistung. Positiv = Entladen, negativ = Laden.",
("BATTERY","voltage"):"Aktuelle DC-Spannung des Batteriesystems.",
("BATTERY","current"):"Aktueller DC-Strom des Batteriesystems.",
("BATTERY","temperature"):"Aktuelle Batterietemperatur für Diagnose und thermische Überwachung.",
("BATTERY","soh"):"State of Health des Batteriesystems als Maß für den verfügbaren Gesundheitszustand.",
("BUFFER","temperature_top"):"Temperatur ganz oben im Pufferspeicher.",
("BUFFER","temperature_upper"):"Temperatur im oberen Bereich des Puffers; häufig Einschalt- bzw. TPO-Fühler.",
("BUFFER","temperature_middle"):"Temperatur in der Mitte des Pufferspeichers.",
("BUFFER","temperature_lower"):"Temperatur im unteren Bereich des Puffers; häufig Ausschalt- bzw. TPM-Fühler.",
("BUFFER","temperature_bottom"):"Temperatur ganz unten im Pufferspeicher.",
("BUFFER","volume"):"Nennvolumen des Pufferspeichers. Wird später zur Abschätzung des thermischen Energieinhalts verwendet.",
("COMBINED_STORAGE","dhw_temperature"):"Warmwassertemperatur des Kombispeichers, getrennt von den Schichttemperaturen.",
("DHW","temperature"):"Aktuelle Warmwasser-Isttemperatur.",
("DHW","target_temperature"):"Gewünschte Warmwasser-Solltemperatur.",
("DHW","temperature_top"):"Temperatur im oberen Bereich des Warmwasserspeichers.",
("DHW","temperature_bottom"):"Temperatur im unteren Bereich des Warmwasserspeichers.",
("DHW","volume"):"Nennvolumen des Warmwasserspeichers.",
("HEATING_CIRCUIT","flow_temperature"):"Aktuelle Vorlauf-Isttemperatur des Heizkreises.",
("HEATING_CIRCUIT","target_flow_temperature"):"Von der Regelung geforderte Vorlauf-Solltemperatur des Heizkreises.",
("HEATING_CIRCUIT","return_temperature"):"Aktuelle Rücklauftemperatur des Heizkreises.",
("HEATING_CIRCUIT","pump_state"):"Betriebszustand der Heizkreispumpe.",
("HEATING_CIRCUIT","mixer_position"):"Aktuelle Stellung des Heizkreismischers.",
("HEATING_CIRCUIT","room_temperature"):"Gemessene Raumtemperatur, die diesem Heizkreis zugeordnet ist.",
("HEATING_CIRCUIT","room_target_temperature"):"Gewünschte Raum-Solltemperatur für diesen Heizkreis.",
("POWER_TO_HEAT","electrical_power"):"Aktuelle elektrische Leistung des Heizstabs bzw. Power-to-Heat-Geräts.",
("POWER_TO_HEAT","temperature"):"Vom Power-to-Heat-System gemessene relevante Temperatur.",
("POWER_TO_HEAT","target_temperature"):"Temperatur-Sollwert des Power-to-Heat-Systems.",
("HEAT_PUMP","electrical_power"):"Aktuelle elektrische Leistungsaufnahme der Wärmepumpe.",
("HEAT_PUMP","thermal_power"):"Aktuell abgegebene thermische Leistung der Wärmepumpe.",
("HEAT_PUMP","flow_temperature"):"Aktuelle Vorlauftemperatur der Wärmepumpe.",
("HEAT_PUMP","return_temperature"):"Aktuelle Rücklauftemperatur der Wärmepumpe.",
("HEAT_PUMP","source_temperature"):"Temperatur der Wärmequelle der Wärmepumpe.",
("HEAT_PUMP","compressor_state"):"Aktueller Betriebszustand des Verdichters.",
("PELLET_BOILER","boiler_temperature"):"Aktuelle Kesseltemperatur des Pelletkessels.",
("PELLET_BOILER","flame_temperature"):"Aktuelle Flammraumtemperatur des Pelletkessels.",
("PELLET_BOILER","flow_temperature"):"Aktuelle Vorlauftemperatur des Pelletkessels.",
("PELLET_BOILER","return_temperature"):"Aktuelle Rücklauftemperatur des Pelletkessels.",
("PELLET_BOILER","fuel_consumption_today"):"Vom Kessel ermittelter Pelletverbrauch des heutigen Tages.",
("PELLET_BOILER","fuel_consumption_yesterday"):"Vom Kessel ermittelter Pelletverbrauch des Vortages.",
("PELLET_BOILER","fuel_consumption_total"):"Fortlaufender Gesamt-Pelletverbrauch, sofern vom Kessel bereitgestellt.",
("PELLET_BOILER","state"):"Aktueller Betriebszustand bzw. Betriebsmodus des Pelletkessels.",
("ROOM","temperature"):"Aktuelle Raumtemperatur.",
("ROOM","humidity"):"Aktuelle relative Raumluftfeuchte.",
("ROOM","co2"):"Aktuelle CO₂-Konzentration der Raumluft.",
("ROOM","heating_target"):"Gewünschte Raumtemperatur.",
("ROOM","valve_position"):"Aktuelle Öffnung des Heizkörper- bzw. Zonenventils.",
("LOAD","power"):"Aktuelle elektrische Leistung eines einzelnen Verbrauchers oder einer Verbrauchergruppe.",
("LOAD","energy_today"):"Heute verbrauchte elektrische Energie dieses Verbrauchers.",
("LOAD","energy_total"):"Fortlaufender Energieverbrauchszähler dieses Verbrauchers.",
("LOAD","state"):"Aktueller Betriebszustand des Verbrauchers.",
}

def point_description(component:str,point:str)->str:
    return DESCRIPTIONS.get((component_type(component),point),"Optionaler INS-EI-Datenpunkt für Monitoring, Diagnose oder Optimierung.")
