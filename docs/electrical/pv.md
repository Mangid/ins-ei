# PV v0.1

The optimizer can use a total PV view while monitoring individual arrays, inverters and strings.

## Total

- pv.total.power — W — OPTIMIZATION
- pv.total.energy_today — kWh — optional
- pv.total.energy_total — kWh — optional
- pv.nominal_power — W — configuration

## Arrays

Zero or more arrays may describe different orientations.

- id / name
- orientation / azimuth
- tilt
- nominal_power
- power — optional

This supports installations with e.g. south/east/west generators and improves forecasting.

## Inverters and strings

Optional DIAGNOSTIC data:

- inverter.power
- inverter.status
- inverter.temperature
- string.voltage — V
- string.current — A
- string.power — W (measured or derived)

String data is not required for optimization but is valuable for central service and fault detection.

## Forecast

PV forecast is a separate time series with timestamped power/energy slots, source, generation time and quality. Optimizers must not depend on a specific forecast provider.
