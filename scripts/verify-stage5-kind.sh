#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cluster_name=ai-sre-stage5
acceptance_dir=$(mktemp -d /tmp/ai-sre-stage5-kind.XXXXXX)
gateway_pid=""
database_url=${STAGE5_DATABASE_URL:-postgresql://ai_sre:local-development-only@127.0.0.1:5432/ai_sre}

cleanup() {
  if [[ -n "${gateway_pid}" ]]; then
    kill "${gateway_pid}" 2>/dev/null || true
    wait "${gateway_pid}" 2>/dev/null || true
  fi
  kind delete cluster --name "${cluster_name}" >/dev/null 2>&1 || true
  case "${acceptance_dir}" in
    /tmp/ai-sre-stage5-kind.*) rm -rf -- "${acceptance_dir}" ;;
  esac
}
trap cleanup EXIT

command -v kind >/dev/null
if [[ -z "${STAGE5_DATABASE_URL:-}" ]]; then
  docker compose -f "${repo_root}/compose.yaml" up -d --wait postgres
fi
kind create cluster \
  --name "${cluster_name}" \
  --wait 120s \
  --kubeconfig "${acceptance_dir}/kubeconfig"

docker exec -i "${cluster_name}-control-plane" kubectl apply -f - <<'YAML'
apiVersion: v1
kind: Namespace
metadata:
  name: ai-sre-test
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: remediation-fixture
  namespace: ai-sre-test
spec:
  replicas: 0
  revisionHistoryLimit: 10
  selector:
    matchLabels:
      app: remediation-fixture
  template:
    metadata:
      labels:
        app: remediation-fixture
      annotations:
        acceptance.ai-sre/version: v1
    spec:
      containers:
        - name: fixture
          image: registry.k8s.io/pause:3.10
YAML
docker exec "${cluster_name}-control-plane" kubectl patch deployment remediation-fixture \
  --namespace ai-sre-test --type merge \
  --patch '{"spec":{"template":{"metadata":{"annotations":{"acceptance.ai-sre/version":"v2"}}}}}'

(cd "${repo_root}/services/tool-gateway" && go build -o "${acceptance_dir}/tool-gateway" ./cmd/server)
env \
  GATEWAY_AUTH_TOKEN=stage5-kind-token \
  SERVER_ADDRESS=127.0.0.1:18085 \
  GRPC_ADDRESS=127.0.0.1:19095 \
  DATABASE_URL="${database_url}" \
  MUTATION_ALLOWED_NAMESPACE=ai-sre-test \
  PROMETHEUS_URL=http://127.0.0.1:1 \
  LOKI_URL=http://127.0.0.1:1 \
  TEMPO_URL=http://127.0.0.1:1 \
  RELEASE_EVENTS_FILE="${repo_root}/testbed/artifacts/fault-events/events.jsonl" \
  GIT_REPOSITORY_PATH="${repo_root}" \
  KUBECONFIG="${acceptance_dir}/kubeconfig" \
  ARTIFACT_DIRECTORY="${acceptance_dir}/artifacts" \
  "${acceptance_dir}/tool-gateway" >"${acceptance_dir}/gateway.log" 2>&1 &
gateway_pid=$!

gateway_ready=false
for _ in {1..30}; do
  if curl -fsS http://127.0.0.1:18085/health/ready >/dev/null 2>&1; then
    gateway_ready=true
    break
  fi
  if ! kill -0 "${gateway_pid}" 2>/dev/null; then
    gateway_status=1
    if wait "${gateway_pid}"; then
      gateway_status=0
    else
      gateway_status=$?
    fi
    gateway_pid=""
    echo "tool gateway exited before becoming ready (status ${gateway_status})" >&2
    tail -n 200 "${acceptance_dir}/gateway.log" >&2
    if [[ "${gateway_status}" -eq 0 ]]; then
      gateway_status=1
    fi
    exit "${gateway_status}"
  fi
  sleep 1
done
if [[ "${gateway_ready}" != true ]]; then
  echo "tool gateway did not become ready within 30 seconds" >&2
  tail -n 200 "${acceptance_dir}/gateway.log" >&2
  exit 1
fi

uv run --project "${repo_root}/services/investigation" \
  python "${repo_root}/scripts/verify-stage5.py" \
  --database-url "${database_url}" \
  --target 127.0.0.1:19095 \
  --token stage5-kind-token

replicas=$(docker exec "${cluster_name}-control-plane" kubectl get deployment remediation-fixture \
  --namespace ai-sre-test -o jsonpath='{.spec.replicas}')
version=$(docker exec "${cluster_name}-control-plane" kubectl get deployment remediation-fixture \
  --namespace ai-sre-test -o jsonpath='{.spec.template.metadata.annotations.acceptance\.ai-sre/version}')
test "${replicas}" = "2"
test "${version}" = "v1"
