# Site, Weather and Rooms v0.1

## SITE

Local configuration may contain:

- latitude
- longitude
- elevation
- timezone

Exact coordinates are needed locally for weather/solar calculations but do not have to be transmitted to INS-Service.

## ENVIRONMENT

Optional measurements:

- outdoor_temperature
- outdoor_humidity
- solar_irradiance
- wind_speed

## WEATHER FORECAST

Timestamped forecast points may include temperature, humidity, cloud cover, solar irradiance and wind speed plus source, generation time and quality.

## ROOM

Zero or more rooms may provide:

- id / name
- temperature
- humidity
- CO2
- air quality
- occupancy
- window_open
- heating_target
- valve_position
- heating_demand
- heating_circuit_id

Room data can begin as MONITORING and later be promoted to OPTIMIZATION when validated.
