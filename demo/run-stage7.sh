#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_dir"
export COMPOSE_PROGRESS=plain

for command in docker curl jq; do
  command -v "$command" >/dev/null || { echo "missing command: $command" >&2; exit 2; }
done
for variable in AI_SRE_MODEL_BASE_URL AI_SRE_MODEL_API_KEY AI_SRE_MODEL_ID; do
  [[ -n "${!variable:-}" ]] || { echo "$variable is required" >&2; exit 2; }
done

recovered=false
recover() {
  if [[ "$recovered" == false ]]; then
    ./testbed/scripts/fault.sh recover payment >/dev/null 2>&1 || true
  fi
}
trap recover EXIT

quiet_make() {
  local target=$1
  local log="artifacts/demo-${target}.log"
  if ! make "$target" >"$log" 2>&1; then
    tail -n 200 "$log" >&2
    return 1
  fi
  echo "$target: passed"
}

quiet_make eval-offline
jq '{passed,dataset,gate_profile,profiles:[.profiles[] | {prompt_version,model_id,completion_rate:.metrics.completion_rate,top1_accuracy:.metrics.top1_accuracy,failed_cases:(.cases | map(select(.failure_categories | length > 0)) | length)}]}' artifacts/stage6-report.json
quiet_make testbed-up
quiet_make compose-up

./testbed/scripts/fault.sh inject errors-payment
for _ in 1 2 3 4 5; do
  curl --silent --output /dev/null \
    --request POST \
    --header 'Content-Type: application/json' \
    --data '{"sku":"widget-red","quantity":1,"amount_cents":1299}' \
    http://localhost:18080/checkout || true
done

# Wait for two Prometheus scrape intervals and the OTel log batch flush.
sleep 12

end=$(date -u +%Y-%m-%dT%H:%M:%SZ)
start=$(date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%SZ)
payload=$(jq -n \
  --arg start "$start" \
  --arg end "$end" \
  '{alert:{alert_id:"DEMO-S7-ERROR-PAYMENT",service:"payment",severity:"critical",summary:"Payment 5xx rate reached 100% in the isolated testbed",source_ref:"demo://stage7/errors-payment",time_window:{start:$start,end:$end}}}')
created=$(curl --fail-with-body --silent --show-error \
  --request POST \
  --header 'Content-Type: application/json' \
  --data "$payload" \
  http://localhost:8000/api/v1/investigations)
investigation_id=$(jq -r '.investigation.investigation_id' <<<"$created")
echo "investigation: $investigation_id"

for _ in $(seq 1 180); do
  snapshot=$(curl --fail-with-body --silent --show-error \
    "http://localhost:8000/api/v1/investigations/$investigation_id")
  status=$(jq -r '.status' <<<"$snapshot")
  case "$status" in
    COMPLETED|FAILED|CANCELLED) break ;;
  esac
  sleep 1
done
[[ "$status" == "COMPLETED" ]] || { echo "investigation ended as $status" >&2; exit 1; }
jq '{status,trace_id:.investigation.trace_id,hypotheses:[.report.hypotheses[] | {rank,statement,confidence,verification_status,supporting_evidence_ids}],evidence:[.report.evidence[] | {evidence_id,source_type,query,reliability,content_excerpt:(.content_excerpt[0:240])}],evidence_gaps:.report.evidence_gaps,budget_usage:.report.budget_usage}' <<<"$snapshot"

evidence_id=$(jq -r '.report.hypotheses[0].supporting_evidence_ids[0] // empty' <<<"$snapshot")
action=$(jq -n --arg evidence_id "$evidence_id" '{action:{action_id:"act-demo-restart-payment",tool_name:"kubernetes.restart_deployment",namespace:"ai-sre-test",name:"payment",description:"Restart the isolated payment Deployment",expected_effect:"Payment error rate decreases",rollback_plan:"Roll back to the previous revision",evidence_ids:([$evidence_id] | map(select(length > 0))),verification_promql:"sum(rate(testbed_http_server_requests_total{service_name=\"payment\",http_response_status_code=~\"5..\"}[5m]))",recovery_goal:"decrease"}}')
approval=$(curl --fail-with-body --silent --show-error \
  --request POST \
  --header 'Content-Type: application/json' \
  --header 'X-Actor-ID: demo-investigator' \
  --header 'X-Actor-Role: investigator' \
  --data "$action" \
  "http://localhost:8000/api/v1/investigations/$investigation_id/approvals")
approval_id=$(jq -r '.approval_id' <<<"$approval")

# Expected negative path: a pending approval cannot execute, even with a fabricated token.
http_status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
  --request POST \
  --header 'Content-Type: application/json' \
  --header 'X-Actor-ID: demo-approver' \
  --header 'X-Actor-Role: approver' \
  --data '{"approval_token":"00000000000000000000000000000000","idempotency_key":"demo-stage7-blocked"}' \
  "http://localhost:8000/api/v1/investigations/$investigation_id/approvals/$approval_id/execute")
[[ "$http_status" == "403" ]] || { echo "expected blocked execution, got HTTP $http_status" >&2; exit 1; }
echo "unapproved execution: blocked (HTTP 403)"

curl --fail-with-body --silent --show-error \
  --request POST \
  --header 'X-Actor-ID: demo-approver' \
  --header 'X-Actor-Role: approver' \
  "http://localhost:8000/api/v1/investigations/$investigation_id/approvals/$approval_id/reject" | jq '{approval_id,status}'

./testbed/scripts/fault.sh recover payment
recovered=true
make testbed-smoke

echo "Web investigation: http://localhost:5173/?investigation=$investigation_id"
echo "Web quality report: http://localhost:5173/ (select 质量报告)"
echo "For real restart/scale/rollback validation, run: make test-stage5-kind"
