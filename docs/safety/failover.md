# Failover and Restore

Failover is a core safety function, not merely an optimizer-off switch.

Before INS-EI actively changes a writable control point, the pre-INS-EI value must be captured as a persistent baseline and verified where possible.

## Failover sequence

1. Stop all new INS-EI control actions.
2. Load persistent baselines for all INS-EI-controlled points.
3. Restore baseline values.
4. Read back and verify each restore where supported.
5. Record success/failure per point.
6. Enter FAILOVER_LOCKED.
7. Require explicit manual release before ACTIVE operation may resume.

The restore target is the pre-INS-EI baseline, not the last value written by INS-EI.

A component without a defined and tested restore path may operate in read-only/SHADOW mode but must not be actively controlled.
