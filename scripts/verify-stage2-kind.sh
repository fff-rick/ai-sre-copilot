#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cluster_name=ai-sre-stage2
acceptance_dir=$(mktemp -d /tmp/ai-sre-stage2-kind.XXXXXX)
gateway_pid=""

cleanup() {
  if [[ -n "${gateway_pid}" ]]; then
    kill "${gateway_pid}" 2>/dev/null || true
    wait "${gateway_pid}" 2>/dev/null || true
  fi
  kind delete cluster --name "${cluster_name}" >/dev/null 2>&1 || true
  case "${acceptance_dir}" in
    /tmp/ai-sre-stage2-kind.*) rm -rf -- "${acceptance_dir}" ;;
  esac
}
trap cleanup EXIT

command -v kind >/dev/null
kind create cluster \
  --name "${cluster_name}" \
  --wait 120s \
  --kubeconfig "${acceptance_dir}/kubeconfig"

docker exec -i "${cluster_name}-control-plane" kubectl apply -f - <<'YAML'
apiVersion: v1
kind: Namespace
metadata:
  name: ai-sre-stage2
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gateway-fixture
  namespace: ai-sre-stage2
spec:
  replicas: 0
  selector:
    matchLabels:
      app: gateway-fixture
  template:
    metadata:
      labels:
        app: gateway-fixture
    spec:
      containers:
        - name: fixture
          image: registry.k8s.io/pause:3.10
---
apiVersion: v1
kind: Event
metadata:
  name: gateway-fixture-warning
  namespace: ai-sre-stage2
involvedObject:
  apiVersion: apps/v1
  kind: Deployment
  name: gateway-fixture
  namespace: ai-sre-stage2
reason: Stage2Acceptance
message: fixed read tool acceptance event
source:
  component: stage2-acceptance
type: Warning
YAML

(cd "${repo_root}/services/tool-gateway" && go build -o "${acceptance_dir}/tool-gateway" ./cmd/server)
env \
  GATEWAY_AUTH_TOKEN=stage2-kind-token \
  SERVER_ADDRESS=127.0.0.1:18083 \
  GRPC_ADDRESS=127.0.0.1:19093 \
  PROMETHEUS_URL=http://127.0.0.1:1 \
  LOKI_URL=http://127.0.0.1:1 \
  TEMPO_URL=http://127.0.0.1:1 \
  RELEASE_EVENTS_FILE="${repo_root}/testbed/artifacts/fault-events/events.jsonl" \
  GIT_REPOSITORY_PATH="${repo_root}" \
  KUBECONFIG="${acceptance_dir}/kubeconfig" \
  ARTIFACT_DIRECTORY="${acceptance_dir}/artifacts" \
  "${acceptance_dir}/tool-gateway" >"${acceptance_dir}/gateway.log" 2>&1 &
gateway_pid=$!

for _ in {1..30}; do
  if curl -fsS http://127.0.0.1:18083/health/ready >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl -fsS http://127.0.0.1:18083/health/ready >/dev/null

uv run --project "${repo_root}/services/investigation" \
  python "${repo_root}/scripts/tool_gateway_kind_e2e.py" \
  --target 127.0.0.1:19093 \
  --token stage2-kind-token
