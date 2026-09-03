# Payment database saturation runbook

Use this runbook when payment requests time out and database pool wait duration increases.

## Confirm

Correlate payment request errors with `db_pool_wait_seconds`, PostgreSQL spans and logs containing
`connection pool exhausted`. Confirm that the fault window overlaps; an old warning is not sufficient.

## Respond

First reduce nonessential test traffic and identify slow transactions. Do not increase the connection
pool before checking the database connection limit because doing so can amplify saturation. Any restart,
scale or configuration change requires approval.
