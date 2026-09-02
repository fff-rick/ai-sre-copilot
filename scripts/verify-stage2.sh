#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
acceptance_dir=$(mktemp -d /tmp/ai-sre-stage2.XXXXXX)
gateway_pid=""

cleanup() {
  if [[ -n "${gateway_pid}" ]]; then
    kill "${gateway_pid}" 2>/dev/null || true
    wait "${gateway_pid}" 2>/dev/null || true
  fi
  case "${acceptance_dir}" in
    /tmp/ai-sre-stage2.*) rm -rf -- "${acceptance_dir}" ;;
  esac
}
trap cleanup EXIT

curl -fsS http://127.0.0.1:19090/-/ready >/dev/null
curl -fsS http://127.0.0.1:13100/loki/api/v1/status/buildinfo >/dev/null
curl -fsS http://127.0.0.1:13200/status/version >/dev/null

(cd "${repo_root}/services/tool-gateway" && go build -o "${acceptance_dir}/tool-gateway" ./cmd/server)
env \
  GATEWAY_AUTH_TOKEN=stage2-acceptance-token \
  SERVER_ADDRESS=127.0.0.1:18082 \
  GRPC_ADDRESS=127.0.0.1:19092 \
  PROMETHEUS_URL=http://127.0.0.1:19090 \
  LOKI_URL=http://127.0.0.1:13100 \
  TEMPO_URL=http://127.0.0.1:13200 \
  RELEASE_EVENTS_FILE="${repo_root}/testbed/artifacts/fault-events/events.jsonl" \
  GIT_REPOSITORY_PATH="${repo_root}" \
  ARTIFACT_DIRECTORY="${acceptance_dir}/artifacts" \
  "${acceptance_dir}/tool-gateway" >"${acceptance_dir}/gateway.log" 2>&1 &
gateway_pid=$!

for _ in {1..30}; do
  if curl -fsS http://127.0.0.1:18082/health/ready >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl -fsS http://127.0.0.1:18082/health/ready >/dev/null

uv run --project "${repo_root}/services/investigation" \
  python "${repo_root}/scripts/tool_gateway_e2e.py" \
  --target 127.0.0.1:19092 \
  --token stage2-acceptance-token
