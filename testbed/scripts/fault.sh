#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
testbed_dir=$(cd -- "$script_dir/.." && pwd)
compose=(docker compose -f "$testbed_dir/compose.yaml")

usage() {
  echo "usage: $0 inject <scenario> | recover <service> | status <service>" >&2
  echo "scenarios: latency-inventory, errors-payment, cpu-order, memory-payment, pool-inventory, dependency-payment, config-payment-path, release-payment" >&2
  exit 2
}

request_control() {
  local service=$1
  local body=$2
  "${compose[@]}" exec -T "$service" wget -q -O - \
    --header='Content-Type: application/json' \
    --header='X-Testbed-Control: stage1-local' \
    --post-data="$body" \
    http://127.0.0.1:8080/_test/fault
}

request_status() {
  local service=$1
  "${compose[@]}" exec -T "$service" wget -q -O - \
    --header='X-Testbed-Control: stage1-local' \
    http://127.0.0.1:8080/_test/fault
}

validate_service() {
  case "$1" in
    api|order|inventory|payment) ;;
    *) echo "unsupported service: $1" >&2; exit 2 ;;
  esac
}

record_event() {
  local event_type=$1
  local scenario=$2
  local target=$3
  local occurred_at=$4
  local response=$5
  mkdir -p "$testbed_dir/artifacts/fault-events"
  printf '{"event_type":"%s","scenario":"%s","target":"%s","occurred_at":"%s","response":%s}\n' \
    "$event_type" "$scenario" "$target" "$occurred_at" "$response" \
    >> "$testbed_dir/artifacts/fault-events/events.jsonl"
}

action=${1:-}
case "$action" in
  inject)
    scenario=${2:-}
    case "$scenario" in
      latency-inventory)
        target=inventory
        body='{"scenario_id":"GT-S1-001","type":"latency","duration_seconds":120,"latency_ms":2500}'
        ;;
      errors-payment)
        target=payment
        body='{"scenario_id":"GT-S1-002","type":"error_rate","duration_seconds":120,"error_rate_percent":100}'
        ;;
      cpu-order)
        target=order
        body='{"scenario_id":"GT-S1-003","type":"cpu_saturation","duration_seconds":120,"cpu_workers":2}'
        ;;
      memory-payment)
        target=payment
        body='{"scenario_id":"GT-S1-004","type":"memory_pressure","duration_seconds":120,"memory_megabytes":64}'
        ;;
      pool-inventory)
        target=inventory
        body='{"scenario_id":"GT-S1-005","type":"connection_pool","duration_seconds":120,"pool_wait_ms":2500}'
        ;;
      dependency-payment)
        target=order
        body='{"scenario_id":"GT-S1-006","type":"dependency_unavailable","duration_seconds":120,"dependency":"payment"}'
        ;;
      config-payment-path)
        target=order
        body='{"scenario_id":"GT-S1-007","type":"configuration_error","duration_seconds":120,"config_key":"payment_path","config_value":"/charge-v2"}'
        ;;
      release-payment)
        target=payment
        body='{"scenario_id":"GT-S1-008","type":"release_regression","duration_seconds":120,"previous_version":"1.0.0","release_version":"1.1.0","trigger_sku":"widget-red"}'
        ;;
      *) usage ;;
    esac
    started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    response=$(request_control "$target" "$body")
    record_event injected "$scenario" "$target" "$started_at" "$response"
    echo "$response"
    ;;
  recover)
    target=${2:-}
    validate_service "$target"
    recovered_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    previous=$(request_status "$target")
    response=$(request_control "$target" '{"type":"clear"}')
    if [[ "$previous" != '{"status":"clear"}' ]]; then
      recovery_payload=$(printf '{"cleared_fault":%s,"result":%s}' "$previous" "$response")
      record_event recovered manual "$target" "$recovered_at" "$recovery_payload"
    fi
    echo "$response"
    ;;
  status)
    target=${2:-}
    validate_service "$target"
    request_status "$target"
    ;;
  *) usage ;;
esac
