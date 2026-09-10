#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
state_dir="$repo_root/.pd-demo"
compose=(docker compose -f "$repo_root/compose.yaml" -f "$repo_root/deploy/pd-demo/copilot.override.yaml")

require() {
  command -v "$1" >/dev/null 2>&1 || { echo "pd-demo requires $1" >&2; exit 1; }
}

up() {
  require docker; require kubectl; require curl
  mkdir -p "$state_dir"
  kubectl cluster-info >/dev/null
  local context
  context=$(kubectl config current-context)
  [[ -n "$context" ]] || { echo "pd-demo requires a current Kubernetes context" >&2; exit 1; }
  docker build -t ai-sre-pd-testbed:local "$repo_root/testbed"
  kubectl config view --raw --minify --flatten > "$state_dir/kubeconfig"
  cp "$state_dir/kubeconfig" "$state_dir/kubeconfig.gateway"
  # A Compose container cannot reach a loopback API endpoint in the host kubeconfig.
  # Docker Desktop's normal kubernetes.docker.internal endpoint needs no rewrite.
  local api_server
  api_server=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')
  case "$api_server" in
    https://127.0.0.1:*|https://localhost:*)
      sed -i "s#${api_server}#https://host.docker.internal:${api_server##*:}#" "$state_dir/kubeconfig.gateway"
      ;;
  esac
  kubectl --kubeconfig "$state_dir/kubeconfig" apply -k "$repo_root/deploy/pd-demo"
  kubectl --kubeconfig "$state_dir/kubeconfig" -n pd-demo rollout status deployment/database --timeout=180s
  kubectl --kubeconfig "$state_dir/kubeconfig" -n pd-demo rollout status deployment/tempo --timeout=180s
  kubectl --kubeconfig "$state_dir/kubeconfig" -n pd-demo rollout status deployment/loki --timeout=180s
  kubectl --kubeconfig "$state_dir/kubeconfig" -n pd-demo rollout status daemonset/otel-collector --timeout=180s
  kubectl --kubeconfig "$state_dir/kubeconfig" -n pd-demo rollout status deployment/prometheus --timeout=180s
  kubectl --kubeconfig "$state_dir/kubeconfig" -n pd-demo rollout status deployment/grafana --timeout=180s
  for deployment in inventory payment order api; do
    kubectl --kubeconfig "$state_dir/kubeconfig" -n pd-demo rollout status "deployment/$deployment" --timeout=180s
  done
  start_port_forwards
  curl --fail --silent --show-error http://127.0.0.1:18080/health/ready >/dev/null
  curl --fail --silent --show-error -X POST http://127.0.0.1:18080/checkout -H 'Content-Type: application/json' -d '{"sku":"widget-blue","quantity":1,"amount_cents":1299}' >/dev/null
  cat <<'EOF'
pd-demo environment is ready.
  Demo API:   http://localhost:18080
  Grafana:    http://localhost:13000
  Prometheus: http://localhost:19090
  Loki:       http://localhost:13100
  Tempo:      http://localhost:13200

To connect Copilot separately, run:
  make pd-demo-copilot-up

Inject a safe, time-bounded fault with:
  make pd-demo-fault ARGS='inject errors-payment'
Then create an investigation for service "payment" in the Copilot UI.
EOF
}

start_port_forward() {
  local name=$1 mapping=$2 logfile="$state_dir/port-forward-$1.log"
  nohup setsid kubectl --kubeconfig "$state_dir/kubeconfig" -n pd-demo port-forward --address 0.0.0.0 "service/$name" "$mapping" >"$logfile" 2>&1 < /dev/null &
  echo $! >> "$state_dir/port-forward.pids"
}

start_port_forwards() {
  stop_port_forwards
  : > "$state_dir/port-forward.pids"
  start_port_forward api 18080:8080
  start_port_forward grafana 13000:3000
  start_port_forward prometheus 19090:9090
  start_port_forward loki 13100:3100
  start_port_forward tempo 13200:3200
  sleep 1
}

stop_port_forwards() {
  [[ -f "$state_dir/port-forward.pids" ]] || return 0
  while read -r pid; do
    kill "$pid" 2>/dev/null || true
  done < "$state_dir/port-forward.pids"
  rm -f "$state_dir/port-forward.pids"
}

down() {
  stop_port_forwards
  if [[ -f "$state_dir/kubeconfig" ]]; then
    kubectl --kubeconfig "$state_dir/kubeconfig" delete namespace pd-demo --ignore-not-found --wait=true
  fi
  rm -rf "$state_dir"
}

copilot_up() {
  [[ -f "$state_dir/kubeconfig.gateway" ]] || {
    echo "pd-demo is not running; start it first with: make pd-demo" >&2
    exit 1
  }
  "${compose[@]}" up --build -d --wait
  echo "Copilot is ready at http://localhost:5173 and is connected to pd-demo."
}

copilot_down() {
  "${compose[@]}" down
}

case "${1:-}" in
  up) up ;;
  down) down ;;
  copilot-up) copilot_up ;;
  copilot-down) copilot_down ;;
  *) echo "usage: $0 up|down|copilot-up|copilot-down" >&2; exit 2 ;;
esac
