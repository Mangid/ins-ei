# INS-EI Architecture

## Principles

INS-EI is local-first. Measurement processing, forecasting inputs, optimization and control must continue locally without INS-Service, MQTT, Internet or remote access.

The core is vendor-neutral. Manufacturer-specific Home Assistant entities are mapped by adapters to an abstract INS-EI model.

A component or data point that is not configured is simply unavailable and must not influence optimization.

INS-EI distinguishes:

- **SUPPORTED** — the abstract point is known by INS-EI.
- **AVAILABLE** — this installation provides the point.
- **USED** — a current optimizer actually uses the point.

Data roles:

- **OPTIMIZATION** — may be used for decisions.
- **MONITORING** — collected for visibility, analysis and learning.
- **DIAGNOSTIC** — collected for service, health and fault detection.

Pilot systems run SHADOW first. Active control is enabled only when the relevant actions, constraints and restore paths are known and tested.

## Layers

1. Home Assistant integrations and devices
2. Adapter / entity mapping
3. Abstract INS-EI site model
4. Collector and data-quality layer
5. Electrical and thermal optimizers
6. Action layer
7. Local audit / failover
8. Optional telemetry to INS-Service

## Site model

A site is composed of optional components, data points, topology, actions and constraints. The optimizer must never contain customer-specific entity IDs or manufacturer-specific logic.

## Safety

Every writable control point must support baseline capture, restore and verification before ACTIVE mode is allowed. Failover stops new INS-EI actions, restores pre-INS-EI baseline values and remains locked until explicitly released.
