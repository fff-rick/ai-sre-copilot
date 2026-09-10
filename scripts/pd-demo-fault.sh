#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
kubeconfig="$repo_root/.pd-demo/kubeconfig"
namespace=pd-demo

[[ -f "$kubeconfig" ]] || { echo "pd-demo is not running; use make pd-demo first" >&2; exit 1; }

request() {
  local service=$1 body=$2
  kubectl --kubeconfig "$kubeconfig" -n "$namespace" exec "deployment/$service" -- \
    wget -q -O - --header='Content-Type: application/json' --header='X-Testbed-Control: stage1-local' \
    --post-data="$body" http://127.0.0.1:8080/_test/fault
}

case "${1:-}" in
  inject)
    case "${2:-}" in
      latency-inventory) request inventory '{"scenario_id":"PD-001","type":"latency","duration_seconds":120,"latency_ms":2500}' ;;
      errors-payment) request payment '{"scenario_id":"PD-002","type":"error_rate","duration_seconds":120,"error_rate_percent":100}' ;;
      cpu-order) request order '{"scenario_id":"PD-003","type":"cpu_saturation","duration_seconds":120,"cpu_workers":2}' ;;
      memory-payment) request payment '{"scenario_id":"PD-004","type":"memory_pressure","duration_seconds":120,"memory_megabytes":64}' ;;
      pool-inventory) request inventory '{"scenario_id":"PD-005","type":"connection_pool","duration_seconds":120,"pool_wait_ms":2500}' ;;
      dependency-payment) request order '{"scenario_id":"PD-006","type":"dependency_unavailable","duration_seconds":120,"dependency":"payment"}' ;;
      config-payment-path) request order '{"scenario_id":"PD-007","type":"configuration_error","duration_seconds":120,"config_key":"payment_path","config_value":"/charge-v2"}' ;;
      release-payment) request payment '{"scenario_id":"PD-008","type":"release_regression","duration_seconds":120,"previous_version":"1.0.0","release_version":"1.1.0","trigger_sku":"widget-red"}' ;;
      *) echo "unknown scenario" >&2; exit 2 ;;
    esac ;;
  recover)
    case "${2:-}" in api|order|inventory|payment) request "$2" '{"type":"clear"}' ;; *) echo "usage: $0 recover <api|order|inventory|payment>" >&2; exit 2;; esac ;;
  *) echo "usage: $0 inject <scenario> | recover <service>" >&2; exit 2 ;;
esac
