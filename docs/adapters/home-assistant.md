# Home Assistant Adapter v0.1

The first Home Assistant adapter is deliberately read-only. It maps arbitrary HA entity IDs to abstract INS-EI component points. Manufacturer names and customer-specific entity IDs must not enter optimizer code.

Each read produces a normalized DataPoint with value, unit, timestamp, quality, source and role. HA states unknown/unavailable become UNAVAILABLE; configured age limits can mark values STALE. Scaling and sign inversion are supported.

GRID convention: positive = import, negative = export. BATTERY power convention: positive = discharge, negative = charge.

The pilot adapter has no write API. Active control will be introduced separately only together with action capabilities, persistent baseline capture, verification and failover restore.
