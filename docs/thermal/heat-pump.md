# HEAT_PUMP v0.1

Types may include AIR_WATER, BRINE_WATER, GROUNDWATER, DIRECT_EXPANSION and OTHER.

## Electrical

- electrical_power — W
- electrical_energy_today/total — kWh
- optional phase voltage/current diagnostics

## Thermal

- thermal_power — W
- thermal_energy_today/total — kWh
- flow_temperature — °C
- return_temperature — °C
- flow_rate — L/min
- source_temperature_in/out — °C

## State

- compressor_active
- heating_active
- dhw_active
- cooling_active
- defrost_active
- compressor_frequency
- operating state

## Targets

- flow_target_temperature
- dhw_target_temperature
- room_target_temperature

Readable and writable capabilities are separate.

## Diagnostics

- compressor_starts
- compressor_runtime
- pressure values where available
- alarm state/code

## Auxiliary heater

Auxiliary resistance heat is modeled separately:

- aux_heater.active
- aux_heater.power
- aux_heater.energy

## Control

Possible abstract capabilities include enable, operating mode, targets and SG-Ready states. Manufacturer-specific implementation belongs in adapters.

With electrical and thermal measurements INS-EI may derive COP and longer-term performance metrics.
