# Changelog

## 0.23.1 — 2026-09-21
- Restored visibility of empty repeatable components so the first heating-circuit instance can be created.

## 0.23.0 — 2026-09-21
- Added heating-demand scoring to SHADOW operating-profile detection.
- Heating-circuit target flow temperature and pump state now contribute to HEATING/TRANSITION/SUMMER evidence.

## 0.22.x — 2026-09-21
- Added WEATHER/outdoor_temperature to the Point Catalog, Discovery and mapping UI.
- Added outdoor temperature as an operating-profile signal.
- Added dedicated Environment & Weather mapping group and outdoor-sensor discovery variants.

## 0.21.x — 2026-09-21
- Added SHADOW-only automatic operating-profile detection and traceable reasons.
- Fixed profile initialization.

## 0.20.x — 2026-09-21
- Reworked strategy UI around automatic HEATING, TRANSITION and SUMMER profiles.
- Added independent per-profile priorities.
- Separated Mappings and Strategy into distinct UI views.

## 0.19.x — 2026-09-21
- Added persistent customer strategy configuration.
- Added customer priorities for thermal storage, battery economics, export and EV charging.
- Added profile-independent DHW minimum requirement.
- Added documented customer-facing Strategy UI.

## 0.18.x — 2026-09-21
- Added timing context for future cheapest and most expensive market slots.

## 0.17.x — 2026-09-21
- Added EPEX future price-series ingestion from Home Assistant sensor attributes.
- Added relative current-price classification and transparent price-context logging.
- Replaced fixed price thresholds in SHADOW battery policy with future-price context.

## 0.16.x — 2026-09-21
- Added first forecast- and price-aware battery SHADOW policy.
- Added MARKET source diagnostics and future-price-series discovery.

## 0.15.x — 2026-09-21
- Added PV and consumption forecast balance to SHADOW reasoning.
- Added FORECAST source/quality diagnostics and singleton mapping normalization.
- Treated forecast values as state data between forecast updates.

## 0.14.x — 2026-09-21
- Connected MARKET tariff configuration and FORECAST inputs to SHADOW.
- Added EPEX/aWATTar and Victron forecast Discovery support.
- Added effective import/export price validation and correct hourly market-price freshness.

## 0.13.x — 2026-09-21
- Split import and export tariff configuration.
- Added static, dynamic and Home Assistant sensor tariff modes.
- Added thematic mapping groups, search, mapped/unmapped filters and collapsible UI.

## 0.12.x — 2026-09-21
- Added provider-based MARKET configuration foundation and persistent market API.

## 0.9.x and earlier — 2026-09-20
- Established component configuration, Point Catalog, mapping UI, Discovery, electrical/battery model and Thermal Core v1.
- Added repeatable HEATING_CIRCUIT, ROOM and LOAD instances and mapping hot-reload.
