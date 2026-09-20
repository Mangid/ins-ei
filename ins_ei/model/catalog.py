"""Canonical component kinds and point names for INS-EI v0.1.

This catalog is deliberately vendor-neutral. A Home Assistant adapter maps real
entities to these names. Presence is optional unless a feature explicitly
requires a point.
"""

COMPONENT_KINDS = {
    "GRID", "PV", "BATTERY", "LOAD", "TARIFF",
    "BUFFER", "DHW_STORAGE", "COMBINED_STORAGE", "HEATING_CIRCUIT",
    "HEAT_PUMP", "PELLET_BOILER", "POWER_TO_HEAT",
    "ROOM", "ENVIRONMENT", "WEATHER_FORECAST", "PV_FORECAST",
}

GRID_POINTS = {
    "power", "import_energy", "export_energy", "voltage_l1", "voltage_l2",
    "voltage_l3", "current_l1", "current_l2", "current_l3", "power_l1",
    "power_l2", "power_l3", "frequency",
}

PV_POINTS = {"power", "energy_today", "energy_total"}
BATTERY_POINTS = {
    "soc", "power", "voltage", "current", "temperature", "soh", "cycles",
    "energy_charged_total", "energy_discharged_total",
}
THERMAL_STORAGE_POINTS = {
    "temperature_top", "temperature_upper", "temperature_middle",
    "temperature_lower", "temperature_bottom",
}
