import { describe, expect, it } from "vitest";

import { formatCreatedAt, presentStatus } from "./status";

describe("presentStatus", () => {
  it("maps known statuses to chip state + label", () => {
    expect(presentStatus("ready")).toEqual({ state: "ok", label: "Ready" });
    expect(presentStatus("needs_review")).toEqual({
      state: "warn",
      label: "Needs review",
    });
    expect(presentStatus("failed")).toEqual({
      state: "blocked",
      label: "Failed",
    });
    expect(presentStatus("awaiting_upload")).toEqual({
      state: "pending",
      label: "Awaiting upload",
    });
    expect(presentStatus("analyzing")).toEqual({
      state: "pending",
      label: "Analyzing",
    });
  });

  it("surfaces unknown wire values rather than inventing a name", () => {
    expect(presentStatus("mystery_state")).toEqual({
      state: "pending",
      label: "mystery_state",
    });
  });
});

describe("formatCreatedAt", () => {
  it("returns a dash for empty, and keeps invalid ISO as-is", () => {
    expect(formatCreatedAt("")).toBe("—");
    expect(formatCreatedAt("not-a-date")).toBe("not-a-date");
  });

  it("formats a valid ISO timestamp", () => {
    const out = formatCreatedAt("2026-07-28T12:10:00.000Z");
    expect(out).not.toBe("—");
    expect(out.length).toBeGreaterThan(4);
  });
});
