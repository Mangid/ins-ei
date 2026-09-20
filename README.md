# INS-EI

INS-EI is a local energy-intelligence platform for Home Assistant installations.

> Status: early development / pilot architecture.

## Core principles

- **Local first:** measurement processing, optimization and control run locally at the customer site.
- **Vendor neutral:** optimizers use an abstract INS-EI data model instead of manufacturer-specific Home Assistant entity IDs.
- **Optional components:** a component that is not configured is not part of the optimization.
- **Safe by design:** active control requires a known and verified restore path. Failover stops INS-EI control and restores captured pre-INS-EI baseline values.
- **Observable:** inputs, data quality, forecasts, decisions, reasons, actions and results are logged for traceability.
- **Service independent:** loss of INS-Service, MQTT, Internet or remote access must not stop local optimization.
- **Shadow first:** pilot installations initially collect data and calculate decisions without issuing control commands.

## Planned thermal topologies

1. **DIRECT** — no buffer; heat generator supplies heating circuits directly, with optional separate DHW storage.
2. **BUFFER** — buffer is the thermal hub; heating circuits and optional DHW charging draw from it.
3. **COMBINED_STORAGE** — buffer and domestic hot-water function share one combined thermal storage system.

Special hydraulic/control solutions are modeled through explicit actions and connections rather than customer-specific optimizer code.

## Architecture

```text
Home Assistant devices/entities
          |
          v
   Adapter / Mapping
          |
          v
 Abstract INS-EI model
          |
   +------+------+
   |             |
Electrical     Thermal
Optimizer      Optimizer
   |             |
   +------+------+
          |
      Action layer
          |
          v
 Home Assistant devices

INS-EI Core ---- telemetry ----> INS-Service
     |
     +---- local audit / logging
     +---- failover / restore
```

## Initial modules

- Core/runtime
- Abstract data model
- Home Assistant adapter
- Collector and data-quality handling
- Electrical optimizer
- Thermal optimizer
- Action layer
- Failover/baseline restore
- INS-Service client and MQTT telemetry
- Licensing/installation identity
- Forecast/site context

## Pilot phase

The first deployments are intended to run in **SHADOW** mode on multiple Home Assistant installations. Pilot telemetry may be more detailed than normal production telemetry so the abstract model and optimization logic can be validated against real systems.

## Security

Never commit customer credentials, MQTT passwords, Home Assistant tokens, WireGuard keys, license secrets, precise customer locations or other secrets/personal data to this repository.
