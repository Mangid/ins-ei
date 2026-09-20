# INS-Service Telemetry

INS-EI optimization and control remain local. INS-Service is used for licensing/identity, health/service and optional telemetry/analysis.

MQTT or any other service connection must never be required for local optimization.

Pilot installations may transmit richer normalized telemetry for model development.

Suggested telemetry profiles:

- MINIMAL — health/version/license/error summaries
- STANDARD — selected normalized measurements and optimizer events
- DEVELOPMENT — broad mapped telemetry, forecasts, prices, quality and optimizer inputs/outputs
- DEBUG — temporary deep diagnostics

Telemetry should use abstract INS-EI point names rather than raw manufacturer-specific entities, while DEVELOPMENT data may include the original source entity as metadata for traceability.

A local store-and-forward queue should preserve relevant telemetry during connectivity outages.

INS-Service analysis should be able to reconstruct a timeline of measurements, forecast, optimizer decision, reason, action/shadow action and resulting measured outcome.
