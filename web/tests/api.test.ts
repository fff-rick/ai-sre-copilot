import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createInvestigation,
  getEvidence,
  getInvestigation,
  getLatestEvaluation,
  getTimeline,
  approveApproval,
  executeApproval,
  listInvestigations,
  listApprovals,
  modifyApproval,
  proposeApproval,
  rejectApproval,
} from "../src/api";

describe("investigation API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("unwraps list, timeline and evidence responses", async () => {
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const path = String(input);
      const value = path.includes("evaluations")
        ? { dataset: "stage6-faults-v1" }
        : path.includes("timeline")
          ? { items: [{ event_id: 1 }] }
          : path.includes("evidence")
            ? { evidence: { evidence_id: "ev-1" } }
            : path.includes("?limit")
              ? { items: [{ status: "COMPLETED" }] }
              : { status: "COMPLETED" };
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => value,
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    expect(await listInvestigations()).toHaveLength(1);
    expect((await getInvestigation("inv /1")).status).toBe("COMPLETED");
    expect(await getTimeline("inv /1")).toHaveLength(1);
    expect((await getEvidence("inv /1", "ev /1")).evidence_id).toBe("ev-1");
    expect((await getLatestEvaluation()).dataset).toBe("stage6-faults-v1");
    await createInvestigation({
      service: "payment",
      severity: "critical",
      summary: "errors",
      sourceRef: "test://web",
    });
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes("inv%20%2F1")),
    ).toBe(true);
  });

  it("maps non-success responses to a safe error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({ ok: false, status: 404, json: async () => ({}) }),
      ),
    );
    await expect(getInvestigation("missing")).rejects.toThrow(
      "Request failed (404)",
    );
  });

  it("uses bounded remediation routes and trusted identity headers", async () => {
    const fetchMock = vi.fn(
      (...args: [string | URL | Request, RequestInit?]) => {
        void args;
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({}),
        });
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const action = {
      action_id: "act-restart-payment",
      tool_name: "kubernetes.restart_deployment" as const,
      namespace: "ai-sre-test",
      name: "payment",
      description: "restart",
      expected_effect: "recover",
      rollback_plan: "rollback",
      evidence_ids: [],
      verification_promql: "up",
      recovery_goal: "decrease" as const,
    };
    await listApprovals("inv /1");
    await proposeApproval("inv /1", action);
    await modifyApproval("inv /1", "apr /1", action);
    await approveApproval("inv /1", "apr /1");
    await rejectApproval("inv /1", "apr /1");
    await executeApproval("inv /1", "apr /1", "token", "idem-stage5-test");
    expect(fetchMock).toHaveBeenCalledTimes(6);
    expect(fetchMock.mock.calls[1]?.[1]?.method).toBe("POST");
    expect(fetchMock.mock.calls[2]?.[1]?.method).toBe("PUT");
    expect(fetchMock.mock.calls[3]?.[1]?.headers).toMatchObject({
      "X-Actor-Role": "approver",
    });
    expect(String(fetchMock.mock.calls[5]?.[0])).toContain(
      "apr%20%2F1/execute",
    );
  });
});
