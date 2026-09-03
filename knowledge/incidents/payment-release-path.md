# Incident: payment callback path regression

An application release changed the callback path from `/payment/callback` to `/payments/callback`,
while the upstream order service retained the old route. Payment returned 404 responses immediately
after deployment.

## Diagnostic signature

The error start time matched the release event, traces ended at the payment HTTP handler, and resource
metrics remained healthy. The resolution was an approved rollback followed by a contract test for the
callback path.
