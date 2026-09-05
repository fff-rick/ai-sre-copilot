import { describe, expect, it } from "vitest";
import { displayTime, displayLabel } from "../src/locale";

describe("Chinese display conventions", () => {
  it("uses Beijing time across date boundaries and tolerates invalid timestamps", () => {
    expect(displayTime("2026-09-03T16:05:09Z")).toBe("2026/09/04 00:05:09");
    expect(displayTime("invalid")).toBe("时间未知");
  });
  it("preserves unknown machine identifiers and localizes known status values", () => {
    expect(displayLabel("unknown.source")).toBe("unknown.source");
    expect(displayLabel("supported")).toBe("证据支持");
    expect(displayLabel("node.collect.completed")).toBe("本阶段处理完成");
  });
});
