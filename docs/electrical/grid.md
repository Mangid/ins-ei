# GRID v0.1

## Core point

| Point | Unit | Role | Notes |
|---|---:|---|---|
| grid.power | W | OPTIMIZATION | + import, - export |

## Optional measurements

- grid.import_energy — kWh
- grid.export_energy — kWh
- grid.voltage_l1/l2/l3 — V
- grid.current_l1/l2/l3 — A
- grid.power_l1/l2/l3 — W
- grid.frequency — Hz

## Configuration / derived values

- grid.import_limit — W, technical or contractual limit
- grid.export_limit — W
- grid.peak_import_power — W, derived according to configured averaging window

Import limits and measured peaks are intentionally modeled because future optimization may include peak shaving and power-based network charges.
