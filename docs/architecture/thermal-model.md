# INS-EI Thermal Model v1

INS-EI models thermal systems by function, not manufacturer.

## Supported topologies

### DIRECT
No buffer storage. Heat generator supplies one or more heating circuits directly. A separate DHW storage may exist.

### BUFFER
A buffer is the thermal center. Heat generators and Power-to-Heat charge the buffer; heating circuits and, where applicable, a separate DHW storage draw heat from it.

### COMBINED_STORAGE
Space-heating buffer and domestic hot water are represented by one combined storage. The model includes thermal stratification points plus a dedicated DHW temperature.

## Component model

Optional components are omitted when not installed. Missing components therefore do not participate in optimization.

- BUFFER
- COMBINED_STORAGE
- DHW
- HEATING_CIRCUIT (repeatable, e.g. heating_circuit_1)
- PELLET_BOILER
- HEAT_PUMP
- POWER_TO_HEAT
- ROOM

All physical Home Assistant entities map to these abstract components and points through the central Point Catalog.

## Principles

1. Thermal topology is installation metadata.
2. Sensor mappings remain manufacturer-independent.
3. Optimization uses only available and quality-approved points.
4. Monitoring and diagnostic points may be collected without affecting optimization.
5. Multiple heating circuits and room sensors are repeatable components.
6. Special solutions remain separate capabilities/actions and must not distort the standard hydraulic topology.
