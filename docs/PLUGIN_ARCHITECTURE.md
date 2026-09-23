# INS-EI plugin architecture

Manufacturer plugins normalize device-specific data into the canonical INS-EI model.

## Principle

INS-EI optimization must not depend on vendor entity names, register numbers or a specific device model.

A plugin therefore has four responsibilities:

1. detect manufacturer, family, model/firmware and capabilities;
2. select a model/controller profile when known;
3. translate raw device data to canonical INS-EI component points;
4. retain unknown raw values as `unmapped` instead of silently discarding or guessing them.

## Model variants

A manufacturer plugin contains multiple profiles. Different OekoFEN controller generations and plant configurations can therefore expose different raw keys while producing the same canonical points. The same applies to my-PV AC-THOR, AC-THOR 9s and AC ELWA 2.

Unknown models use the `generic` profile. Their raw values remain visible for diagnosis and can later be promoted into a tested profile.

## Canonical boundary

Examples:

- OekoFEN controller value -> `pellet_boiler.runtime`
- OekoFEN buffer sensor -> `buffer.temperature_upper`
- my-PV AC-THOR power -> `power_to_heat.electrical_power`

The optimizer only consumes the canonical side.

## Fallback

Home Assistant entity mappings remain supported for devices without a dedicated plugin. Plugins and HA mappings can coexist in one installation.

## First reference plugins

- OekoFEN
- my-PV (AC-THOR / AC-THOR 9s / AC ELWA 2)

The next implementation phase connects the existing INS OekoFEN and my-PV interfaces to these plugin contracts and adds profile detection from real devices.
