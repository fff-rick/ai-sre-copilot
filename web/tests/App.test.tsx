import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
      if (path.includes("/timeline")) return jsonResponse(timeline);
      if (path.includes("/evidence/")) return jsonResponse({ evidence });
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

  afterEach(() => vi.unstubAllGlobals());

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
});
