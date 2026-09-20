# Thermal Storage v0.1

## BUFFER

Configuration:

- volume — L
- min_temperature — °C
- max_temperature — °C

Optional temperature points:

- temperature_top
- temperature_upper
- temperature_middle
- temperature_lower
- temperature_bottom

Sensors may optionally provide a relative height position (0.0 bottom to 1.0 top). This supports real installations where probes are positioned e.g. at 75%, 50% and 25%.

Derived values may include estimated stored energy, available energy and free thermal capacity.

## DHW_STORAGE

- volume
- temperature and/or top/middle/bottom temperatures
- min_temperature
- target_temperature
- max_temperature
- optional hygiene-cycle metadata

## COMBINED_STORAGE

One physical/thermally coupled store provides space heating and DHW.

It may expose storage-zone temperatures plus a dedicated DHW temperature sensor:

- combined.temperature_top/upper/middle/lower/bottom
- combined.dhw_temperature
- optional DHW top/bottom points
- dhw_min_temperature
- dhw_target_temperature
- dhw_max_temperature

A high buffer-zone temperature must not be treated as proof that DHW temperature is sufficient.
