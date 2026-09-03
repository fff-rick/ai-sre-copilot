# Payment service

Payment owns payment authorization and callback processing. The order service calls payment,
and payment uses PostgreSQL through a bounded connection pool. The on-call team is commerce-sre.

## Signals

Primary signals are `http_requests_total`, request latency, database pool wait duration and timeout
logs. A pool wait increase without matching database CPU saturation usually indicates leaked or slow
connections rather than insufficient application replicas.
