import { describe, expect, it } from "vitest";

import {
  buildCreatePayload,
  contentTypeForFile,
  normalizeBudget,
  safeFilename,
} from "./create-payload";

function fakeFile(
  name: string,
  type: string,
  size = 1024,
): File {
  return new File([new Uint8Array(size)], name, { type });
}

describe("safeFilename", () => {
  it("strips path components", () => {
    expect(safeFilename("/tmp/clip.mp4")).toBe("clip.mp4");
    expect(safeFilename("C:\\\\Videos\\\\a.mov")).toBe("a.mov");
  });

  it("rejects empty / dot names", () => {
    expect(safeFilename("")).toBe("source.mp4");
    expect(safeFilename(".")).toBe("source.mp4");
    expect(safeFilename("..")).toBe("source.mp4");
  });
});

describe("contentTypeForFile", () => {
  it("prefers an allowed File.type", () => {
    expect(contentTypeForFile(fakeFile("x.bin", "video/webm"))).toBe(
      "video/webm",
    );
    expect(contentTypeForFile(fakeFile("x.mp4", "video/mp4"))).toBe(
      "video/mp4",
    );
  });

  it("falls back to extension when type is empty or vague", () => {
    expect(contentTypeForFile(fakeFile("clip.mov", ""))).toBe(
      "video/quicktime",
    );
    expect(contentTypeForFile(fakeFile("clip.mkv", "application/octet-stream"))).toBe(
      "video/x-matroska",
    );
    expect(contentTypeForFile(fakeFile("clip.webm", ""))).toBe("video/webm");
  });

  it("returns null when browser is vague and extension unknown", () => {
    expect(contentTypeForFile(fakeFile("clip", ""))).toBeNull();
    expect(contentTypeForFile(fakeFile("clip.xyz", "application/octet-stream"))).toBeNull();
  });
});

describe("normalizeBudget", () => {
  it("accepts string and number forms (Svelte number bind)", () => {
    expect(normalizeBudget("2.50")).toEqual({ ok: true, value: 2.5 });
    expect(normalizeBudget(2.5)).toEqual({ ok: true, value: 2.5 });
    expect(normalizeBudget("")).toEqual({ ok: true, value: undefined });
    expect(normalizeBudget(undefined)).toEqual({ ok: true, value: undefined });
    expect(normalizeBudget(null)).toEqual({ ok: true, value: undefined });
    expect(normalizeBudget(0).ok).toBe(false);
    expect(normalizeBudget(-1).ok).toBe(false);
  });
});

describe("buildCreatePayload", () => {
  it("shapes a minimal request without budget", () => {
    const file = fakeFile("standoff.mp4", "video/mp4");
    const result = buildCreatePayload({
      title: "  Western standoff  ",
      budgetUsd: "  ",
      file,
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.body).toEqual({
      title: "Western standoff",
      filename: "standoff.mp4",
      content_type: "video/mp4",
    });
    expect("budget_usd" in result.body).toBe(false);
  });

  it("includes a positive budget_usd from string or number", () => {
    const fromStr = buildCreatePayload({
      title: "Clip",
      budgetUsd: "2.50",
      file: fakeFile("a.mov", "video/quicktime"),
    });
    expect(fromStr.ok).toBe(true);
    if (!fromStr.ok) return;
    expect(fromStr.body.budget_usd).toBe(2.5);

    const fromNum = buildCreatePayload({
      title: "Clip",
      budgetUsd: 3,
      file: fakeFile("a.mov", "video/quicktime"),
    });
    expect(fromNum.ok).toBe(true);
    if (!fromNum.ok) return;
    expect(fromNum.body.budget_usd).toBe(3);
  });

  it("rejects empty title, non-positive budget, and unknown type", () => {
    expect(
      buildCreatePayload({
        title: "   ",
        budgetUsd: "",
        file: fakeFile("a.mp4", "video/mp4"),
      }).ok,
    ).toBe(false);

    const badBudget = buildCreatePayload({
      title: "Ok",
      budgetUsd: "0",
      file: fakeFile("a.mp4", "video/mp4"),
    });
    expect(badBudget.ok).toBe(false);
    if (badBudget.ok) return;
    expect(badBudget.error).toMatch(/positive/i);

    const badType = buildCreatePayload({
      title: "Ok",
      budgetUsd: "",
      file: fakeFile("notes.txt", "text/plain"),
    });
    expect(badType.ok).toBe(false);
    if (badType.ok) return;
    expect(badType.error).toMatch(/unrecognized/i);
  });
});
