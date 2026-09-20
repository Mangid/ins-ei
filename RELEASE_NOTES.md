# INS-EI Pilot Release Notes — 0.9.1

**Date:** 2026-09-20  
**Stage:** Internal pilot / SHADOW

## Purpose

Version 0.9.1 establishes the installation-configuration and mapping foundation needed before INS-EI is connected to broader optimization logic.

## Highlights

### Point-centric installation mapping
The UI starts from the data INS-EI understands. Components are grouped, abstract data points are documented, and the installer selects the corresponding Home Assistant entity.

### Real installation structure
Optional equipment can be marked present or absent. Heating circuits, rooms/zones and electrical loads are designed as repeatable instances.

### Discovery remains assistance
Read-only Discovery scans Home Assistant and proposes likely mappings. Mappings remain explicit and editable.

### Electrical, battery and thermal foundation
Grid, PV, battery/BMS diagnostics and Thermal Core v1 are represented through the abstract model. Thermal topologies are DIRECT, BUFFER and COMBINED_STORAGE.

## Pilot limitations

- SHADOW mode remains the intended operating mode.
- Active device control and production failover/restore are not the focus of this milestone.
- Discovery suggestions require installer review.
- Component configuration is currently primarily installation/UI metadata.
- ROOM and LOAD modelling is preliminary and expected to evolve through pilot use.

## Next focus

1. Validate component configuration and mappings on real pilot installations.
2. Refine repeatable heating-circuit, room and load modelling.
3. Complete thermal data mapping and quality checks.
4. Connect the abstract electrical and thermal model to traceable SHADOW optimization decisions.
5. Preserve clear reasoning and before/after evidence for simulated and future executed actions.
