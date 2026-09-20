# BATTERY v0.1

## Optimization points

- battery_system.total.soc — %
- battery_system.total.power — W; + discharge, - charge
- capacity_nominal — kWh
- capacity_usable — kWh
- max_charge_power — W
- max_discharge_power — W
- min_soc — %
- max_soc — %

## Optional monitoring / diagnostics

- voltage — V
- current — A
- temperature — °C
- state_of_health — %
- cycles
- charged_energy_total — kWh
- discharged_energy_total — kWh
- BMS min/max cell voltage
- BMS min/max temperature
- alarms / status

Multiple physical battery units may be represented below a total battery-system view.

## Derived

- energy_available
- energy_free

## Control

Readability and writability are separate capabilities. Possible abstract actions include charge, discharge, hold, auto, power limit and SOC limits.

A readable battery is not automatically controllable. Any writable value is subject to failover baseline capture and verified restore.
