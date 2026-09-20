# TARIFF v0.1

Tariffs are independent from GRID.

## Current

- tariff.import_price — ct/kWh
- tariff.export_price — ct/kWh

## Forecast

Timestamped import/export price slots with start, end and price.

## Optional price components

- energy_market_price
- supplier_markup
- grid_energy_charge
- taxes

## Future power-based charges

- grid_power_charge — €/kW
- grid_peak_window — e.g. 15 minutes

The optimizer consumes normalized effective prices and must not contain supplier-specific logic such as aWATTar or EPEX formulas.
