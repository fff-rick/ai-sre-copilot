# Inventory downstream latency runbook

Use this runbook when inventory P95 latency increases while the inventory process remains healthy.

## Confirm

Inspect slow Tempo spans and dependency edges. If the catalog dependency dominates span duration,
correlate catalog latency and timeout logs before changing inventory capacity. A downstream delay will
not normally be fixed by restarting inventory.

## Respond

Apply the catalog dependency degradation policy and reduce retries to avoid retry amplification. Any
configuration change requires approval and must be followed by an SLI check.
