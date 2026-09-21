# INS-EI Pilot Release Notes — 0.23.1

**Date:** 2026-09-21  
**Stage:** Internal pilot / SHADOW

## Purpose

0.23.1 represents the current integration milestone: real electrical, tariff, forecast and thermal inputs can be mapped into a vendor-neutral SiteModel and evaluated by a traceable SHADOW decision layer. The next focus is robust automatic operating-profile detection and heating-demand modelling.

## Highlights

### Structured installation mapping
Mappings are grouped by electricity, heat, environment/weather, tariffs/forecasts and rooms/loads. Search and mapped/unmapped filters help commissioning. Repeatable heating circuits, rooms and loads can be created as named instances.

### Dynamic market intelligence
Import and export tariffs are independent. This supports static/dynamic combinations. The current pilot can consume an EPEX spot-price entity, calculate effective import/export prices from tariff parameters and read future price slots for relative price evaluation.

### PV and consumption forecasts
Victron PV and consumption forecasts can be mapped for the current hour, today and tomorrow. SHADOW records forecast balance and combines it with current SOC, grid flow and market context.

### Customer strategy
The Strategy view separates customer preferences from technical protection. HEATING, TRANSITION and SUMMER each have their own priorities for thermal storage, battery economics, export and e-mobility. Hard requirements such as minimum DHW temperature remain independent and take precedence.

### Automatic operating profile — SHADOW
INS-EI is beginning to infer HEATING, TRANSITION or SUMMER automatically instead of requiring seasonal manual switching. Outdoor temperature is available as an abstract WEATHER point. Heating-circuit demand and thermal state are being added as additional evidence. The detector currently remains observational so its behavior can be validated before it changes optimization priorities.

## Safety and traceability

- Production actuator commands are not enabled by this milestone.
- Technical protection limits and hard requirements must override customer priorities.
- New optimization behavior is introduced in SHADOW first.
- Price, forecast, data-quality, profile and decision reasons are logged for validation.
- Discovery remains assistance; explicit mappings remain authoritative.

## Current pilot limitations

- Automatic operating-profile detection still needs real heating-circuit mappings and time/hysteresis stabilization.
- Strategy priorities are not yet the final optimization objective function.
- Target-SOC and full multi-hour energy-allocation planning are not yet implemented.
- Power-to-heat availability depends on the mapped device/integration being available.
- Active device control, restore/failover and production safety validation remain future milestones.

## Next focus

1. Map real heating circuits and validate heating-demand signals.
2. Add hysteresis/time stabilization to automatic operating-profile selection.
3. Let the detected profile select its configured customer priorities.
4. Feed those priorities into the multi-hour battery/thermal/EV allocation planner.
5. Preserve a complete explanation of why each proposed action was selected.
