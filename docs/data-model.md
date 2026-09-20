# Abstract Data Model v0.1

All device-facing values are abstract. Missing points are allowed.

Every measured data point carries at least:

- value
- unit
- timestamp
- quality: GOOD, STALE, UNAVAILABLE or INVALID
- source
- role: OPTIMIZATION, MONITORING or DIAGNOSTIC

## Electrical domains

- GRID
- PV
- BATTERY
- LOAD
- TARIFF

## Thermal domains

- THERMAL_STORAGE
- DHW_STORAGE
- COMBINED_STORAGE
- HEATING_CIRCUIT
- HEAT_PUMP
- PELLET_BOILER
- POWER_TO_HEAT

## Environment

- SITE
- WEATHER / WEATHER_FORECAST
- ROOM

## Convention

Units use SI where practical. Power is represented in W internally; energy in Wh or kWh only where explicitly specified by the point definition. Component-specific sign conventions are documented with the component.
