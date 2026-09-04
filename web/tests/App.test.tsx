import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/App";

const evidence = {
  evidence_id: "ev-0123456789abcdef",
  source_type: "knowledge.runbook",
  source_ref: "repo://knowledge/runbook.md#chunk=kc-1",
  query: { text: "payment timeout" },
  observed_at: "2026-09-03T10:00:05Z",
  content_excerpt: "Connection pool exhausted after slow transactions.",
  content_hash: "a".repeat(64),
  structured_facts: null,
  reliability: "medium",
};

function record(status = "COMPLETED") {
  return {
    investigation: {
      investigation_id: "inv-stage4",
      trace_id: "0123456789abcdef",
      alert: {
        alert_id: "payment-errors",
        service: "payment",
        severity: "critical",
        summary: "Payment error rate increased",
        source_ref: "alertmanager://payment",
        time_window: {
          start: "2026-09-03T09:50:00Z",
          end: "2026-09-03T10:00:00Z",
        },
      },
      created_at: "2026-09-03T10:00:00Z",
    },
    status,
    report:
      status === "COMPLETED"
        ? {
            impact_summary: "Payment error rate increased",
            hypotheses: [
              {
                hypothesis_id: "hyp-1-pool",
                statement: "Database connections are exhausted.",
                rank: 1,
                confidence: 0.91,
                supporting_evidence_ids: [evidence.evidence_id],
                contradicting_evidence_ids: [],
                verification_status: "supported",
                next_checks: [],
              },
            ],
            evidence: [evidence],
            evidence_gaps: [],
            uncertainty: [],
            completed_at: "2026-09-03T10:00:10Z",
          }
        : null,
    last_error: null,
  };
}

const timeline = {
  items: [
    {
      event_id: 1,
      investigation_id: "inv-stage4",
      event_type: "investigation.created",
      status: "RECEIVED",
      payload: {},
      created_at: "2026-09-03T10:00:00Z",
    },
    {
      event_id: 2,
      investigation_id: "inv-stage4",
      event_type: "node.collect.completed",
      status: "COLLECTING",
      payload: { node: "collect", evidence_count: 5 },
      created_at: "2026-09-03T10:00:05Z",
    },
  ],
  next_event_id: 2,
};

const evaluation = {
  schema_version: 1,
  dataset: "stage6-faults-v1",
  dataset_sha256: "a".repeat(64),
  mode: "replay",
  commit: "b".repeat(40),
  generated_at: "2026-09-04T00:00:00Z",
  gate_profile: "evidence-first-v3",
  passed: true,
  gate_failures: [],
  comparison: {
    top1_accuracy_delta: 0.25,
    top3_accuracy_delta: 0,
    token_cost_proxy_change: 0.1,
  },
  profiles: [
    {
      prompt_version: "evidence-first-v3",
      prompt_sha256: "c".repeat(64),
      model_id: "frozen-replay-model-v1",
      metrics: {
        case_count: 32,
        completion_rate: 1,
        top1_accuracy: 1,
        top3_accuracy: 1,
        evidence_validity: 1,
        unsupported_claim_rate: 0,
        read_tool_success_rate: 1,
        p50_duration_seconds: 0.01,
        p95_duration_seconds: 0.02,
        input_tokens: 100,
        output_tokens: 50,
        p50_cost_usd: 0.001,
        p95_cost_usd: 0.002,
        security_pass_rate: 1,
        trace_completeness: 1,
      },
      family_metrics: {
        latency: {
          case_count: 4,
          completion_rate: 1,
          top1_accuracy: 1,
          top3_accuracy: 1,
          evidence_validity: 1,
          unsupported_claim_rate: 0,
          read_tool_success_rate: 1,
          p50_duration_seconds: 0.01,
          p95_duration_seconds: 0.02,
          input_tokens: 10,
          output_tokens: 5,
          p50_cost_usd: 0.001,
          p95_cost_usd: 0.002,
          security_pass_rate: 1,
          trace_completeness: 1,
        },
      },
      gate_failures: [],
      failed_cases: [],
    },
  ],
};

class FakeEventSource {
  static latest: FakeEventSource | null = null;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listener: (() => void) | null = null;
  close = vi.fn();

  constructor(public url: string) {
    FakeEventSource.latest = this;
  }

  addEventListener(_name: string, listener: () => void) {
    this.listener = listener;
  }
}

function jsonResponse(value: unknown, ok = true) {
  return Promise.resolve({
    ok,
    status: ok ? 200 : 503,
    json: async () => value,
  });
}

function installFetch(getStatus: () => string = () => "COMPLETED") {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path === "/api/v1/evaluations/latest") {
        return jsonResponse(evaluation);
      }
      if (path.includes("/timeline")) return jsonResponse(timeline);
      if (path.includes("/evidence/")) return jsonResponse({ evidence });
      if (path.includes("/approvals")) return jsonResponse([]);
      if (path === "/api/v1/investigations?limit=100") {
        return jsonResponse({ items: [record(getStatus())] });
      }
      return jsonResponse(record(getStatus()));
    }),
  );
}

describe("App", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/");
    vi.stubGlobal("EventSource", FakeEventSource);
    FakeEventSource.latest = null;
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders the persistent investigation, hypotheses, timeline and evidence drawer", async () => {
    installFetch();
    render(<App />);

    expect(
      await screen.findByText("Payment error rate increased"),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("Database connections are exhausted."),
    ).toBeInTheDocument();
    expect(screen.getByText("5 份证据")).toBeInTheDocument();
    expect(screen.getByText("持久快照")).toBeInTheDocument();
    expect(window.location.search).toContain("investigation=inv-stage4");

    fireEvent.click(screen.getByRole("button", { name: evidence.evidence_id }));
    expect(
      await screen.findByRole("complementary", { name: "证据详情" }),
    ).toBeInTheDocument();
    expect(screen.getByText(evidence.content_excerpt)).toBeInTheDocument();
    expect(screen.getByText(/payment timeout/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭证据详情" }));
    expect(
      screen.queryByRole("complementary", { name: "证据详情" }),
    ).not.toBeInTheDocument();
  });

  it("subscribes for active work and returns to the durable snapshot at completion", async () => {
    let currentStatus = "COLLECTING";
    installFetch(() => currentStatus);
    render(<App />);

    expect(
      await screen.findByText("调查正在进行，结论将在校验后写入。"),
    ).toBeInTheDocument();
    await waitFor(() => expect(FakeEventSource.latest).not.toBeNull());
    expect(FakeEventSource.latest?.url).toContain("inv-stage4/events");
    FakeEventSource.latest?.onopen?.();
    expect(await screen.findByText("实时同步")).toBeInTheDocument();
    FakeEventSource.latest?.onerror?.();
    expect(await screen.findByText("正在重连")).toBeInTheDocument();

    currentStatus = "COMPLETED";
    FakeEventSource.latest?.listener?.();
    expect(await screen.findByText("持久快照")).toBeInTheDocument();
    expect(FakeEventSource.latest?.close).toHaveBeenCalled();
  });

  it("surfaces list failures without inventing empty state data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => jsonResponse({}, false)),
    );
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "无法读取调查列表",
    );
  });

  it("runs the approval, parameter update, execution and rejection controls", async () => {
    let approvals: Array<Record<string, unknown>> = [];
    let counter = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string | URL | Request, init?: RequestInit) => {
        const path = String(input);
        const method = init?.method ?? "GET";
        if (path.includes("/timeline")) return jsonResponse(timeline);
        if (path === "/api/v1/investigations?limit=100") {
          return jsonResponse({ items: [record()] });
        }
        if (path.endsWith("/approvals") && method === "GET") {
          return jsonResponse(approvals);
        }
        if (path.endsWith("/approvals") && method === "POST") {
          counter += 1;
          const body = JSON.parse(String(init?.body)) as {
            action: Record<string, unknown>;
          };
          const created = {
            approval_id: `apr-${counter}`,
            investigation_id: "inv-stage4",
            action: body.action,
            target: "ai-sre-test/deployment/payment",
            parameters_hash: "a".repeat(64),
            risk_level: "medium",
            status: "PENDING",
            proposed_by: "console-investigator",
            approved_by: null,
            token_expires_at: null,
          };
          approvals = [...approvals, created];
          return jsonResponse(created);
        }
        if (path.endsWith("/approve")) {
          approvals = approvals.map((item) => ({
            ...item,
            status: "APPROVED",
            approved_by: "console-approver",
          }));
          return jsonResponse({
            approval: approvals[0],
            approval_token: "approval-token",
          });
        }
        if (path.endsWith("/execute")) {
          approvals = approvals.map((item) => ({
            ...item,
            status: "CONSUMED",
          }));
          return jsonResponse({
            execution_id: "exec-1",
            status: "SUCCEEDED",
            recovery_status: "RECOVERED",
            pre_evidence: {},
            post_evidence: {},
          });
        }
        if (path.endsWith("/reject")) {
          approvals = approvals.map((item) => ({
            ...item,
            status: "REJECTED",
          }));
          return jsonResponse(approvals.at(-1));
        }
        if (path.includes("/approvals/") && method === "PUT") {
          const action = JSON.parse(String(init?.body)) as Record<
            string,
            unknown
          >;
          approvals = approvals.map((item) => ({
            ...item,
            action,
            status: "PENDING",
          }));
          return jsonResponse(approvals[0]);
        }
        return jsonResponse(record());
      }),
    );
    render(<App />);
    await screen.findByText("Database connections are exhausted.");
    fireEvent.change(screen.getByLabelText("动作"), {
      target: { value: "scale" },
    });
    fireEvent.change(screen.getByLabelText("副本数"), {
      target: { value: "4" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交审批" }));
    expect(
      await screen.findByText("PENDING · medium risk", { exact: false }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "更新参数" }));
    await waitFor(() =>
      expect(
        screen.getByText("PENDING · medium risk", { exact: false }),
      ).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "批准" }));
    expect(
      await screen.findByRole("button", { name: "执行并验证" }),
    ).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "执行并验证" }));
    expect(await screen.findByText("恢复判定 · RECOVERED")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("动作"), {
      target: { value: "rollback" },
    });
    fireEvent.change(screen.getByLabelText("版本号（0=上一版）"), {
      target: { value: "0" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交审批" }));
    expect(
      await screen.findByRole("button", { name: "拒绝" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "拒绝" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "拒绝" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("creates a bounded investigation from the Web console", async () => {
    const fetchMock = vi.fn(
      (input: string | URL | Request, init?: RequestInit) => {
        const path = String(input);
        if (path === "/api/v1/investigations" && init?.method === "POST") {
          const body = JSON.parse(String(init.body)) as {
            alert: { service: string; summary: string; time_window: object };
          };
          expect(body.alert.service).toBe("inventory");
          expect(body.alert.summary).toBe("Inventory latency increased");
          expect(body.alert.time_window).toBeDefined();
          return jsonResponse(record("COLLECTING"));
        }
        if (path === "/api/v1/investigations?limit=100") {
          return jsonResponse({ items: [record()] });
        }
        if (path.includes("/timeline")) return jsonResponse(timeline);
        if (path.includes("/approvals")) return jsonResponse([]);
        return jsonResponse(record());
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await screen.findByText("Payment error rate increased");
    fireEvent.change(screen.getByLabelText("服务"), {
      target: { value: "inventory" },
    });
    fireEvent.change(screen.getByLabelText("告警摘要"), {
      target: { value: "Inventory latency increased" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建调查" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/investigations",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("renders the versioned quality report and returns to investigations", async () => {
    installFetch();
    render(<App />);
    expect(
      (await screen.findAllByText("Payment error rate increased")).length,
    ).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "质量报告" }));
    expect(await screen.findByText("stage6-faults-v1")).toBeInTheDocument();
    expect(screen.getByText("PASS")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText("latency")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "调查" }));
    expect(
      (await screen.findAllByText("Payment error rate increased")).length,
    ).toBeGreaterThan(0);
  });

  it("explains how to generate a missing evaluation report", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string | URL | Request) => {
        if (String(input) === "/api/v1/evaluations/latest") {
          return jsonResponse({}, false);
        }
        return jsonResponse({ items: [] });
      }),
    );
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "质量报告" }));
    expect(await screen.findByText("尚无可展示的质量报告")).toBeInTheDocument();
    expect(screen.getByText("make eval-offline")).toBeInTheDocument();
  });
});
