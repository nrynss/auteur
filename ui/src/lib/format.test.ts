import { describe, expect, it } from "vitest";

import { formatCost, formatTimecode } from "./format";

describe("formatTimecode", () => {
  it("formats m:ss.mmm", () => {
    expect(formatTimecode(0)).toBe("0:00.000");
    expect(formatTimecode(59.999)).toBe("0:59.999");
    expect(formatTimecode(10.62)).toBe("0:10.620");
  });

  it("carries rounding across the second boundary", () => {
    // 59.9996s rounds to 60s — must never read 0:59.1000.
    expect(formatTimecode(59.9996)).toBe("1:00.000");
    expect(formatTimecode(0.9996)).toBe("0:01.000");
  });

  it("shows hours past the hour, or when asked", () => {
    expect(formatTimecode(3661.25)).toBe("1:01:01.250");
    expect(formatTimecode(61.5, { hours: true })).toBe("0:01:01.500");
  });

  it("can drop milliseconds", () => {
    expect(formatTimecode(10.62, { ms: false })).toBe("0:10");
    expect(formatTimecode(61.5, { ms: false })).toBe("1:01");
  });

  it("formats negatives with a sign", () => {
    expect(formatTimecode(-1.5)).toBe("-0:01.500");
  });

  it("renders absent values as a dash, never a fabricated number", () => {
    expect(formatTimecode(null)).toBe("—");
    expect(formatTimecode(undefined)).toBe("—");
    expect(formatTimecode(Number.NaN)).toBe("—");
    expect(formatTimecode(Number.POSITIVE_INFINITY)).toBe("—");
  });
});

describe("formatCost", () => {
  it("formats dollars to cents", () => {
    expect(formatCost(0.3)).toBe("$0.30");
    expect(formatCost(5)).toBe("$5.00");
    expect(formatCost(1234.56)).toBe("$1,234.56");
  });

  it("renders absent costs as a dash", () => {
    expect(formatCost(null)).toBe("—");
    expect(formatCost(undefined)).toBe("—");
  });
});
