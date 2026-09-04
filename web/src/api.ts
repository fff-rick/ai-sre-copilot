export type Status =
  | "RECEIVED"
  | "SCOPING"
  | "COLLECTING"
  | "HYPOTHESIZING"
  | "VERIFYING"
  | "RECOMMENDING"
  | "REPORTING"
  | "WAITING_APPROVAL"
  | "EXECUTING"
  | "VALIDATING"
  | "COMPLETED"
  | "CANCELLED"
  | "FAILED";

export interface Evidence {
  evidence_id: string;
  source_type: string;
  source_ref: string;
  query: Record<string, unknown>;
  observed_at: string;
  content_excerpt: string;
  content_hash: string;
  structured_facts: Record<string, unknown> | unknown[] | null;
  reliability: "high" | "medium" | "low";
}

export interface Hypothesis {
  hypothesis_id: string;
  statement: string;
  rank: number;
  confidence: number;
  supporting_evidence_ids: string[];
  contradicting_evidence_ids: string[];
  verification_status: string;
  next_checks: string[];
}

export interface InvestigationReport {
  impact_summary: string;
  hypotheses: Hypothesis[];
  evidence: Evidence[];
  evidence_gaps: Array<{ source_type: string; message: string }>;
  uncertainty: string[];
  completed_at: string;
}

export interface RemediationAction {
  action_id: string;
  tool_name:
    | "kubernetes.restart_deployment"
    | "kubernetes.scale_deployment"
    | "kubernetes.rollback_deployment";
  namespace: string;
  name: string;
  replicas?: number;
  revision?: number;
  description: string;
  expected_effect: string;
  rollback_plan: string;
  evidence_ids: string[];
  verification_promql: string;
  recovery_goal: "decrease" | "increase";
}

export interface RemediationApproval {
  approval_id: string;
  investigation_id: string;
  action: RemediationAction;
  target: string;
  parameters_hash: string;
  risk_level: "low" | "medium" | "high";
  status: "PENDING" | "APPROVED" | "REJECTED" | "EXPIRED" | "CONSUMED";
  proposed_by: string;
  approved_by: string | null;
  token_expires_at: string | null;
}

export interface ApprovalGrant {
  approval: RemediationApproval;
  approval_token: string;
}

export interface RemediationExecution {
  execution_id: string;
  status: string;
  recovery_status: "RECOVERED" | "NOT_RECOVERED" | "UNABLE_TO_DETERMINE";
  pre_evidence: Record<string, unknown>;
  post_evidence: Record<string, unknown>;
}

export interface StoredInvestigation {
  investigation: {
    investigation_id: string;
    trace_id: string;
    alert: {
      alert_id: string;
      service: string;
      severity: "critical" | "warning" | "info";
      summary: string;
      source_ref: string;
      time_window: { start: string; end: string };
    };
    created_at: string;
  };
  status: Status;
  report: InvestigationReport | null;
  last_error: string | null;
}

export type InvestigationSummary = Omit<StoredInvestigation, "report">;

export interface InvestigationEvent {
  event_id: number;
  investigation_id: string;
  event_type: string;
  status: Status;
  payload: {
    node?: string;
    evidence_count?: number;
    hypothesis_count?: number;
    evidence_gap_count?: number;
  };
  created_at: string;
}

export interface EvaluationMetrics {
  case_count: number;
  completion_rate: number;
  top1_accuracy: number;
  top3_accuracy: number;
  evidence_validity: number;
  unsupported_claim_rate: number;
  read_tool_success_rate: number;
  p50_duration_seconds: number;
  p95_duration_seconds: number;
  input_tokens: number;
  output_tokens: number;
  p50_cost_usd: number;
  p95_cost_usd: number;
  security_pass_rate: number;
  trace_completeness: number;
}

export interface EvaluationReport {
  schema_version: number;
  dataset: string;
  dataset_sha256: string;
  mode: string;
  commit: string;
  generated_at: string;
  gate_profile: string;
  passed: boolean;
  gate_failures: string[];
  comparison: {
    top1_accuracy_delta: number;
    top3_accuracy_delta: number;
    token_cost_proxy_change: number;
  };
  profiles: Array<{
    prompt_version: string;
    prompt_sha256: string;
    model_id: string;
    metrics: EvaluationMetrics;
    family_metrics: Record<string, EvaluationMetrics>;
    gate_failures: string[];
    failed_cases: Array<{
      case_id: string;
      failure_categories: string[];
      trace_id: string;
    }>;
  }>;
}

export interface CreateInvestigationInput {
  service: string;
  severity: "critical" | "warning" | "info";
  summary: string;
  sourceRef: string;
}

export async function listInvestigations(): Promise<InvestigationSummary[]> {
  const response = await request<{ items: InvestigationSummary[] }>(
    "/api/v1/investigations?limit=100",
  );
  return response.items;
}

export function createInvestigation(
  input: CreateInvestigationInput,
): Promise<StoredInvestigation> {
  const end = new Date();
  const start = new Date(end.getTime() - 10 * 60 * 1000);
  return request("/api/v1/investigations", {
    method: "POST",
    body: JSON.stringify({
      alert: {
        alert_id: `manual-${crypto.randomUUID()}`,
        service: input.service,
        severity: input.severity,
        summary: input.summary,
        source_ref: input.sourceRef,
        time_window: { start: start.toISOString(), end: end.toISOString() },
      },
    }),
  });
}

export function getLatestEvaluation(): Promise<EvaluationReport> {
  return request("/api/v1/evaluations/latest");
}

export function getInvestigation(id: string): Promise<StoredInvestigation> {
  return request(`/api/v1/investigations/${encodeURIComponent(id)}`);
}

export async function getTimeline(id: string): Promise<InvestigationEvent[]> {
  const response = await request<{ items: InvestigationEvent[] }>(
    `/api/v1/investigations/${encodeURIComponent(id)}/timeline?limit=500`,
  );
  return response.items;
}

export async function getEvidence(
  investigationId: string,
  evidenceId: string,
): Promise<Evidence> {
  const response = await request<{ evidence: Evidence }>(
    `/api/v1/investigations/${encodeURIComponent(investigationId)}/evidence/${encodeURIComponent(evidenceId)}`,
  );
  return response.evidence;
}

const investigator = {
  "X-Actor-ID": "console-investigator",
  "X-Actor-Role": "investigator",
};
const approver = {
  "X-Actor-ID": "console-approver",
  "X-Actor-Role": "approver",
};

export function listApprovals(id: string): Promise<RemediationApproval[]> {
  return request(`/api/v1/investigations/${encodeURIComponent(id)}/approvals`);
}

export function proposeApproval(
  id: string,
  action: RemediationAction,
): Promise<RemediationApproval> {
  return request(`/api/v1/investigations/${encodeURIComponent(id)}/approvals`, {
    method: "POST",
    headers: investigator,
    body: JSON.stringify({ action }),
  });
}

export function modifyApproval(
  investigationId: string,
  approvalId: string,
  action: RemediationAction,
): Promise<RemediationApproval> {
  return request(
    `/api/v1/investigations/${encodeURIComponent(investigationId)}/approvals/${encodeURIComponent(approvalId)}`,
    { method: "PUT", headers: approver, body: JSON.stringify(action) },
  );
}

export function approveApproval(
  investigationId: string,
  approvalId: string,
): Promise<ApprovalGrant> {
  return request(
    `/api/v1/investigations/${encodeURIComponent(investigationId)}/approvals/${encodeURIComponent(approvalId)}/approve`,
    {
      method: "POST",
      headers: approver,
      body: JSON.stringify({ expires_in_seconds: 900 }),
    },
  );
}

export function rejectApproval(
  investigationId: string,
  approvalId: string,
): Promise<RemediationApproval> {
  return request(
    `/api/v1/investigations/${encodeURIComponent(investigationId)}/approvals/${encodeURIComponent(approvalId)}/reject`,
    { method: "POST", headers: approver },
  );
}

export function executeApproval(
  investigationId: string,
  approvalId: string,
  approvalToken: string,
  idempotencyKey: string,
): Promise<RemediationExecution> {
  return request(
    `/api/v1/investigations/${encodeURIComponent(investigationId)}/approvals/${encodeURIComponent(approvalId)}/execute`,
    {
      method: "POST",
      headers: approver,
      body: JSON.stringify({
        approval_token: approvalToken,
        idempotency_key: idempotencyKey,
      }),
    },
  );
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...init.headers,
    },
  });
  if (!response.ok) {
    throw new Error(`Request failed (${response.status})`);
  }
  return (await response.json()) as T;
}
