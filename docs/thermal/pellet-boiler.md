# PELLET_BOILER v0.1

## Configuration

- id / name
- nominal_power — kW
- technology: CONDENSING or NON_CONDENSING
- fuel.type: PELLETS
- fuel.price — €/kg
- fuel.lower_heating_value (Hi) — kWh/kg
- fuel.higher_heating_value (Hs) — kWh/kg

Hi and Hs must remain distinct so efficiency and cost calculations use a consistent reference basis.

## Optional measurements

- state
- burner_active
- current_power
- flow_temperature
- return_temperature
- flue_gas_temperature
- runtime
- burner_starts
- fuel_consumption_rate — kg/h
- fuel_consumption_today — kg
- fuel_consumption_total — kg
- thermal_power — kW
- thermal_energy_today/total — kWh

Where the boiler supplies its own calculated fuel consumption, it is mapped directly. A heat meter, when available, allows measured thermal output and real-system efficiency to be evaluated.

INS-EI may derive normalized heat cost in ct/kWh thermal for comparison with heat pumps and power-to-heat.
