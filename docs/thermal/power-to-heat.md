# POWER_TO_HEAT v0.1

Vendor-neutral representation for devices such as AC-THOR, Ohmpilot and simple heating elements.

Types:

- VARIABLE
- STAGED
- SWITCHED

Configuration:

- id / name
- nominal_power — W
- conversion_efficiency
- thermal target: BUFFER, DHW_STORAGE or COMBINED_STORAGE
- optional target zone: TOP, MIDDLE, BOTTOM

Optional measurements:

- electrical_power
- electrical_energy_today/total
- state
- temperature
- target_temperature
- device diagnostics

Possible control capabilities:

- enable
- power
- power_limit
- target_temperature

POWER_TO_HEAT does not imply a fixed PV-surplus rule. Electrical and thermal optimizers decide whether electricity should be exported, stored electrically, converted to heat, or otherwise used based on constraints and economics.
