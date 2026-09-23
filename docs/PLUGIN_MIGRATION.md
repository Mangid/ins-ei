# Migration of existing INS device interfaces

## Existing OekoFEN interface

The proven `ins_okofen` interface already exposes useful semantic values such as:

- buffer upper temperature (`puffer_oben`)
- second/lower buffer sensor (`puffer_zweiter_fuhler_2`)
- domestic hot water temperature (`warmwasser`)
- boiler temperature and modulation
- burner state / starts
- pellet consumption today / yesterday
- heating-circuit flow actual / target
- pumps and operating states
- outdoor temperature

These values will become an input adapter for the OekoFEN plugin. The plugin then normalizes them to the canonical INS-EI model. Heating circuits must remain multi-instance because an OekoFEN plant can expose several circuits.

## Existing my-PV interface

The proven AC-THOR 9s interface uses:

- local Modbus TCP
- unit 1
- telemetry registers 1057-1084
- HTTP `/data.jsn`
- read-only telemetry as the initial plugin responsibility

Known families are AC-THOR, AC-THOR 9s and AC ELWA 2. Each family has its own profile/capabilities. Telemetry normalization and control are kept separate so a read-only plugin cannot accidentally become an actuator.

## Next step

Move the existing raw protocol readers into plugin transports. Do not duplicate vendor logic in the optimizer or in per-customer mappings. Unknown controller/model values are retained as unmapped raw data until a tested profile is added.
