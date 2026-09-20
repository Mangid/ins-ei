# HEATING_CIRCUIT v0.1

Zero or more heating circuits are supported.

Configuration:

- id / name
- type: RADIATOR, FLOOR_HEATING or OTHER

Optional points:

- flow_temperature — °C
- flow_target_temperature — °C
- return_temperature — °C
- pump_state
- pump_speed — %
- mixing_valve_position — %
- operating_mode
- heating_active
- thermal_power — kW
- thermal_energy_today/total — kWh
- flow_rate — L/min

Optional heating-curve data:

- slope
- offset
- room_target_temperature

Rooms may reference a heating-circuit id. Where flow, return and flow-rate data are available, INS-EI may derive thermal power.
