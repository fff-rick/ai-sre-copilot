#!/usr/bin/env bash
set -euo pipefail

response=$(curl --fail-with-body --silent --show-error \
  --request POST \
  --header 'Content-Type: application/json' \
  --data '{"sku":"widget-blue","quantity":1,"amount_cents":1299}' \
  http://localhost:18080/checkout)

echo "$response"
case "$response" in
  *'"status":"confirmed"'*) ;;
  *) echo "unexpected checkout response" >&2; exit 1 ;;
esac

