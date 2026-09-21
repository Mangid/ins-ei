# INS-EI

INS-EI is a local-first, vendor-neutral energy-intelligence platform for Home Assistant.

> Current pilot version: **0.23.1** — SHADOW mode / active pilot development.

## Current pilot capabilities

- Home Assistant app with Ingress web UI
- Abstract INS-EI Point Catalog and explicit Home Assistant mappings
- Thematic mapping UI: electricity, heat, environment/weather, tariffs/forecasts, rooms/loads
- Search, mapped/unmapped filters and collapsible groups
- Optional components and repeatable HEATING_CIRCUIT, ROOM and LOAD instances
- Read-only Discovery with installer-reviewed suggestions
- Electrical model for grid, PV and battery plus BMS diagnostics
- Thermal model with DIRECT, BUFFER and COMBINED_STORAGE
- MARKET model with independent import/export tariffs
- Dynamic EPEX/aWATTar spot-price input and future price-slot series
- FORECAST inputs for PV generation and consumption
- Traceable SHADOW decisions using SOC, grid flow, prices and forecasts
- Customer strategy layer with separate HEATING, TRANSITION and SUMMER profiles
- SHADOW operating-profile detection using outdoor temperature and heating-demand signals
- Mapping hot-reload without restarting Home Assistant

## Core principles

- **Local first:** processing, optimization and future control run locally.
- **Vendor neutral:** optimization uses abstract INS-EI points, not manufacturer entity IDs.
- **Safe by design:** technical protection limits and hard requirements override customer preferences.
- **SHADOW first:** new decision logic is observed and validated before it can influence devices.
- **Observable:** inputs, quality, prices, forecasts, strategy, decisions and reasons remain traceable.
- **Service independent:** Internet or remote-service loss must not stop local operation.
- **Human-verifiable mapping:** Discovery suggests; explicit mappings remain the source of truth.

## Strategy model

INS-EI separates technical limits, hard requirements and customer priorities.

The customer-facing strategy contains three profiles:
- **HEATING** — prioritizes useful thermal storage and reduction of heating fuel use.
- **TRANSITION** — balances thermal demand, battery economics and export opportunities.
- **SUMMER** — secures required hot water while allowing more economic use/export of surplus energy.

The intended normal mode is automatic profile selection. Manual seasonal switching should not be required. Profile detection is currently SHADOW-only and is being validated from outdoor temperature, heating-circuit demand and thermal state. Hysteresis/time stabilization is a planned next step.

Customer priorities must never override technical protection limits or hard requirements such as minimum DHW temperature.

## Market and forecast model

Import and export tariffs are configured independently, allowing combinations such as static import with dynamic export. Dynamic tariffs can use a Home Assistant spot-price source. For the current EPEX integration INS-EI also reads future price slots from the sensor's `data` attribute and evaluates the current price relative to upcoming slots.

Forecast points currently include PV and consumption for the current hour, today and tomorrow where available. SHADOW can calculate a forecast energy balance and include it in battery reasoning.

## Thermal topologies

- **DIRECT** — heat generator supplies heating circuits directly; separate DHW is optional.
- **BUFFER** — a buffer is the thermal hub.
- **COMBINED_STORAGE** — heating buffer and DHW function share one storage system.

See `docs/architecture/thermal-model.md`.

## Current pilot status

The runtime collects and normalizes real installation data and produces traceable SHADOW decisions. It does **not** yet issue production actuator commands. Current work is focused on robust operating-profile detection, heating-circuit mapping and the later target-SOC/energy-allocation planner.

## Security

Never commit customer credentials, MQTT passwords, Home Assistant tokens, WireGuard keys, license secrets, precise customer locations or other secrets/personal data to this repository.
