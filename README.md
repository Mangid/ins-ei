# INS-EI

INS-EI is a local-first, vendor-neutral energy-intelligence platform for Home Assistant.

> Current pilot version: **0.9.1** — SHADOW mode / early development.

## Current pilot capabilities

- Home Assistant app with Ingress web UI
- Abstract INS-EI Point Catalog
- Point-centric mapping grouped by component
- Persistent mappings and duplicate validation
- Searchable Home Assistant entity picker
- Read-only Discovery with mapping suggestions
- Automatic freshness, unit and role defaults
- Electrical model for grid, PV and battery
- Battery/BMS diagnostic points
- Thermal model with DIRECT, BUFFER and COMBINED_STORAGE
- Optional component configuration
- Repeatable HEATING_CIRCUIT, ROOM and LOAD instances
- Mapping hot-reload without restarting Home Assistant

## Core principles

- **Local first:** processing, optimization and control run locally.
- **Vendor neutral:** optimizers use abstract INS-EI points, not manufacturer entity IDs.
- **Optional components:** absent equipment does not participate in optimization.
- **Safe by design:** active control requires a known restore path; pilot operation is SHADOW first.
- **Observable:** inputs, quality, decisions, reasons, actions and results remain traceable.
- **Service independent:** Internet or remote-service loss must not stop local operation.
- **Human-verifiable mapping:** Discovery suggests; explicit mappings remain the source of truth.

## Thermal topologies

- **DIRECT** — heat generator supplies heating circuits directly; separate DHW is optional.
- **BUFFER** — a buffer is the thermal hub.
- **COMBINED_STORAGE** — heating buffer and DHW function share one storage system.

See the thermal architecture document under docs/architecture/thermal-model.md.

## Current UI model

Mappings are organized by INS-EI component and data point. The installer selects the matching Home Assistant entity. Single components can be enabled/disabled; repeatable components such as heating circuits, rooms/zones and loads can have multiple named instances.

Discovery is an assistance layer, not the source of truth.

## Pilot status

The current implementation collects and normalizes real installation data. Active switching/control is not the goal of the present pilot stage. Next development focuses on completing installation modelling and connecting electrical/thermal data to traceable SHADOW optimization decisions.

## Security

Never commit customer credentials, MQTT passwords, Home Assistant tokens, WireGuard keys, license secrets, precise customer locations or other secrets/personal data to this repository.
