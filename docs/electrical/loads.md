# LOADS v0.1

INS-EI may collect individual loads even when they are not used for optimization.

Each load may provide:

- id / name / category
- power — W
- energy_today — kWh
- energy_total — kWh
- voltage — V
- current — A
- state
- room
- phase

Capabilities:

- controllable
- flexible

Flexible loads may additionally define minimum runtime, maximum runtime, minimum off-time, nominal power, allowed time windows, surplus-only policy and priority.

Examples include monitored appliances and controllable loads such as a dehumidifier that is allowed to run on PV surplus.
