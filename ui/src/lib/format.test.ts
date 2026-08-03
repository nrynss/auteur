import { describe, expect, it } from "vitest";

import {
  formatConfirmCost,
  formatCost,
  formatSeconds,
  formatTimecode,
} from "./format";

describe("formatTimecode", () => {
  it("formats mm:ss.mmm with zero-padded minutes (mockup transport)", () => {
    expect(formatTimecode(0)).toBe("00:00.000");
    expect(formatTimecode(11.42)).toBe("00:11.420");
    expect(formatTimecode(59.999)).toBe("00:59.999");
    expect(formatTimecode(10.62)).toBe("00:10.620");
  });

  it("carries rounding across the second boundary", () => {
    // 59.9996s rounds to 60s — must never read 00:59.1000.
    expect(formatTimecode(59.9996)).toBe("01:00.000");
    expect(formatTimecode(0.9996)).toBe("00:01.000");
  });

  it("shows hours past the hour, or when asked", () => {
    expect(formatTimecode(3661.25)).toBe("1:01:01.250");
    expect(formatTimecode(61.5, { hours: true })).toBe("0:01:01.500");
  });

  it("can drop milliseconds", () => {
    expect(formatTimecode(10.62, { ms: false })).toBe("00:10");
    expect(formatTimecode(61.5, { ms: false })).toBe("01:01");
  });

  it("formats negatives with a sign", () => {
    expect(formatTimecode(-1.5)).toBe("-00:01.500");
  });

  it("renders absent values as a dash, never a fabricated number", () => {
    expect(formatTimecode(null)).toBe("n/a");
    expect(formatTimecode(undefined)).toBe("n/a");
    expect(formatTimecode(Number.NaN)).toBe("n/a");
    expect(formatTimecode(Number.POSITIVE_INFINITY)).toBe("n/a");
  });
});

describe("formatCost", () => {
  it("formats dollars to cents", () => {
    expect(formatCost(0.3)).toBe("$0.30");
    expect(formatCost(5)).toBe("$5.00");
    expect(formatCost(1234.56)).toBe("$1,234.56");
  });

  it("renders absent costs as a dash", () => {
    expect(formatCost(null)).toBe("n/a");
    expect(formatCost(undefined)).toBe("n/a");
  });
});

describe("formatConfirmCost", () => {
  it("never rounds under the echoed confirm value", () => {
    expect(formatConfirmCost(0.0375)).toBe("$0.0375");
    expect(formatConfirmCost(0.034)).toBe("$0.034");
    // Sub-cent must not display as $0.00
    expect(formatConfirmCost(0.004)).toBe("$0.004");
    expect(formatConfirmCost(0)).toBe("$0.00");
  });
});

describe("formatSeconds", () => {
  it("formats raw seconds the way the mockup's scene rail does", () => {
    expect(formatSeconds(10.62)).toBe("10.62");
    expect(formatSeconds(3.2)).toBe("03.20");
    expect(formatSeconds(15.04)).toBe("15.04");
  });

  it("renders absent spans as a dash", () => {
    expect(formatSeconds(null)).toBe("n/a");
    expect(formatSeconds(Number.NaN)).toBe("n/a");
  });
});
