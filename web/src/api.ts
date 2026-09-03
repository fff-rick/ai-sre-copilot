export type Status =
  | "RECEIVED"
  | "SCOPING"
  | "COLLECTING"
  | "HYPOTHESIZING"
  | "VERIFYING"
  | "RECOMMENDING"
  | "REPORTING"
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

export async function listInvestigations(): Promise<InvestigationSummary[]> {
  const response = await request<{ items: InvestigationSummary[] }>(
    "/api/v1/investigations?limit=100",
  );
  return response.items;
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

async function request<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`Request failed (${response.status})`);
  }
  return (await response.json()) as T;
}
