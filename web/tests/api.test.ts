import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getEvidence,
  getInvestigation,
  getTimeline,
  listInvestigations,
} from "../src/api";

describe("investigation API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("unwraps list, timeline and evidence responses", async () => {
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const path = String(input);
      const value = path.includes("timeline")
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
});
