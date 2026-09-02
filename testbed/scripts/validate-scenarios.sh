#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
testbed_dir=$(cd -- "$script_dir/.." && pwd)
fault_script="$script_dir/fault.sh"
checkout_body_file=$(mktemp)

cleanup() {
  rm -f "$checkout_body_file"
  for service in api order inventory payment; do
    "$fault_script" recover "$service" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT

checkout() {
  local sku=${1:-widget-blue}
  curl --silent --show-error \
    --output "$checkout_body_file" \
    --write-out '%{http_code} %{time_total}\n' \
    --request POST \
    --header 'Content-Type: application/json' \
    --data "{\"sku\":\"$sku\",\"quantity\":1,\"amount_cents\":1299}" \
    http://localhost:18080/checkout
}

assert_status() {
  local scenario=$1
  local expected=$2
  local actual=$3
  if [[ "$actual" != "$expected" ]]; then
    echo "$scenario: status=$actual, want=$expected; body=$(cat "$checkout_body_file")" >&2
    exit 1
  fi
}

prom_value() {
  local query=$1
  curl --fail --silent --show-error --get http://localhost:19090/api/v1/query \
    --data-urlencode "query=$query" \
    | jq --exit-status --raw-output '.data.result[0].value[1] | tonumber'
}

wait_for_metric() {
  local query=$1
  local predicate=$2
  local value
  for _ in {1..10}; do
    if value=$(prom_value "$query" 2>/dev/null) && awk "BEGIN {exit !($value $predicate)}"; then
      echo "$value"
      return 0
    fi
    sleep 2
  done
  echo "metric did not satisfy predicate: $query $predicate" >&2
  return 1
}

echo "[1/8] inventory latency"
"$fault_script" inject latency-inventory >/dev/null
read -r status duration < <(checkout)
assert_status latency-inventory 201 "$status"
awk "BEGIN {exit !($duration >= 2.4)}" || { echo "latency-inventory: duration=$duration, want >=2.4" >&2; exit 1; }
"$fault_script" recover inventory >/dev/null

echo "[2/8] payment errors"
"$fault_script" inject errors-payment >/dev/null
read -r status _ < <(checkout)
assert_status errors-payment 502 "$status"
"$fault_script" recover payment >/dev/null

echo "[3/8] order CPU saturation"
"$fault_script" inject cpu-order >/dev/null
wait_for_metric 'testbed_fault_cpu_workers{service_name="order",fault_type="cpu_saturation"}' '== 2' >/dev/null
wait_for_metric 'max(rate(container_cpu_usage_seconds_total{container_label_com_docker_compose_project="ai-sre-testbed",container_label_com_docker_compose_service="order"}[30s]))' '> 0.20' >/dev/null
"$fault_script" recover order >/dev/null

echo "[4/8] payment memory pressure"
memory_before=$(prom_value 'max(container_memory_working_set_bytes{container_label_com_docker_compose_project="ai-sre-testbed",container_label_com_docker_compose_service="payment"})')
"$fault_script" inject memory-payment >/dev/null
wait_for_metric 'testbed_fault_memory_allocated_bytes{service_name="payment",fault_type="memory_pressure"}' '== 67108864' >/dev/null
wait_for_metric 'max(container_memory_working_set_bytes{container_label_com_docker_compose_project="ai-sre-testbed",container_label_com_docker_compose_service="payment"})' "> $((memory_before + 50 * 1024 * 1024))" >/dev/null
"$fault_script" recover payment >/dev/null

echo "[5/8] inventory connection pool exhaustion"
"$fault_script" inject pool-inventory >/dev/null
read -r status duration < <(checkout)
assert_status pool-inventory 502 "$status"
awk "BEGIN {exit !($duration >= 2.4)}" || { echo "pool-inventory: duration=$duration, want >=2.4" >&2; exit 1; }
wait_for_metric 'testbed_db_pool_exhaustions_total{service_name="inventory"}' '>= 1' >/dev/null
"$fault_script" recover inventory >/dev/null

echo "[6/8] payment dependency unavailable"
"$fault_script" inject dependency-payment >/dev/null
read -r status _ < <(checkout)
assert_status dependency-payment 502 "$status"
"$fault_script" recover order >/dev/null

echo "[7/8] invalid payment path configuration"
"$fault_script" inject config-payment-path >/dev/null
read -r status _ < <(checkout)
assert_status config-payment-path 502 "$status"
"$fault_script" recover order >/dev/null

echo "[8/8] payment release regression"
"$fault_script" inject release-payment >/dev/null
read -r status _ < <(checkout widget-red)
assert_status release-payment 502 "$status"
read -r status _ < <(checkout widget-blue)
assert_status release-payment-unaffected-input 201 "$status"
"$fault_script" recover payment >/dev/null

"$script_dir/smoke.sh" >/dev/null
echo "all 8 stage-1 fault scenarios passed"
