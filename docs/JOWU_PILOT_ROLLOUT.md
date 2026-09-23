# JoWu pilot rollout

## Goal

Connect JoWu as the second real INS-EI installation and validate the multi-installation architecture end to end.

## Installation identity

- INS-EI installation ID: `jowu`
- Server: `https://ins-ei.ins-enertech.net`
- Telemetry interval: 30 seconds
- Mode: SHADOW / monitoring only
- No automatic control during the first data-collection phase.

## Known installation

- Fronius GEN24 10 kW
- Battery approx. 12 kWh
- Fronius Ohmpilot 9 kW
- KNV direct-expansion heat pump with mControl2
- Combined storage
- Optional Shelly 3EM for dedicated heat-pump consumption

## Rollout procedure

1. Update/install INS-EI Pilot add-on 0.26.3.
2. Open the INS-EI add-on UI and run Home Assistant discovery.
3. Set installation ID to `jowu`.
4. Set thermal topology to `COMBINED_STORAGE`.
5. Configure the central server:
   - URL: `https://ins-ei.ins-enertech.net`
   - installation ID: `jowu`
   - interval: 30 seconds
   - enabled: true
6. Map only values that are actually available and verified in Home Assistant.
7. Prioritize these canonical INS-EI points:
   - `pv.power`
   - `grid.power`
   - `battery.soc`
   - `battery.power`
   - `dhw.temperature` or the appropriate combined-storage temperatures
   - `weather.outdoor_temperature`
   - Ohmpilot power/temperature where available
   - heat-pump power and operating state when available
8. Keep optimizer/control actions disabled; collect and analyze first.
9. Verify add-on telemetry status shows connected.
10. Verify the central INS-EI web app automatically shows a second installation card named `jowu`.
11. Verify Influx history receives data for tag `installation_id=jowu`.
12. Leave the installation collecting data continuously.

## Acceptance criteria

The pilot is successful when:

- JoWu and niki-home are visible simultaneously in the central web app.
- Both have independent online/last-seen status.
- JoWu telemetry is stored under its own Influx installation tag.
- 24 h / 7 d / 30 d history can be queried without mixing installations.
- Missing device types or points do not break telemetry from the remaining mapped devices.
- No control command is sent to JoWu during the observation phase.

## After JoWu

Use the same rollout path for Kaufmann with installation ID `kaufmann`. Hardware-specific mappings differ, but the server, telemetry schema and central UI remain unchanged.
