import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import * as projects from "../api/projects";
import {
  exportBlockers,
  hasEstimatedCost,
  probeRegenerate,
} from "./regenerate";
import { tryExport } from "./export";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("probeRegenerate — cost-confirm echo", () => {
  it("returns needs_confirm with the exact estimate from a 422 probe", async () => {
    const estimated = 0.0375;
    vi.spyOn(projects, "regenerateEvent").mockRejectedValueOnce(
      new ApiError(
        "Regeneration is billable; confirm the estimated cost",
        "precondition_failed",
        422,
        { estimated_cost_usd: estimated, require: "confirm_cost_usd" },
      ),
    );

    const result = await probeRegenerate("proj_1", 4);
    expect(result).toEqual({
      status: "needs_confirm",
      estimatedCostUsd: estimated,
    });
    expect(projects.regenerateEvent).toHaveBeenCalledWith("proj_1", 4, {});
  });

  it("re-POSTs the exact number as confirm_cost_usd and accepts", async () => {
    const estimated = 0.0375;
    const accepted = {
      run_id: "run_01K",
      kind: "repair",
      status: "running",
      estimated_cost_usd: estimated,
    };
    const spy = vi
      .spyOn(projects, "regenerateEvent")
      .mockResolvedValueOnce(accepted);

    const result = await probeRegenerate("proj_1", 4, {
      confirmCostUsd: estimated,
    });

    expect(result).toEqual({ status: "accepted", accepted });
    expect(spy).toHaveBeenCalledWith("proj_1", 4, {
      confirm_cost_usd: estimated,
    });
    // The echoed value must be bit-identical to the probe — not reformatted.
    const body = spy.mock.calls[0][2];
    expect(body?.confirm_cost_usd).toBe(0.0375);
    expect(body?.confirm_cost_usd).not.toBe(0.04);
  });

  it("accepts free regen (estimate 0) without confirm_cost_usd", async () => {
    const accepted = {
      run_id: "run_free",
      kind: "repair",
      status: "running",
      estimated_cost_usd: 0,
    };
    const spy = vi
      .spyOn(projects, "regenerateEvent")
      .mockResolvedValueOnce(accepted);

    const result = await probeRegenerate("proj_1", 0);
    expect(result.status).toBe("accepted");
    expect(spy).toHaveBeenCalledWith("proj_1", 0, {});
  });

  it("branches on ApiError.code, not message text", async () => {
    vi.spyOn(projects, "regenerateEvent").mockRejectedValueOnce(
      new ApiError(
        "Regeneration is billable; confirm the estimated cost",
        "invalid_request", // wrong code — must NOT look like a cost probe
        400,
        { estimated_cost_usd: 0.5 },
      ),
    );
    const result = await probeRegenerate("proj_1", 1);
    expect(result.status).toBe("error");
    if (result.status === "error") {
      expect(result.error.code).toBe("invalid_request");
    }
  });

  it("does not re-open confirm when confirm was already sent but estimate mismatches", async () => {
    vi.spyOn(projects, "regenerateEvent").mockRejectedValueOnce(
      new ApiError("confirm_cost_usd must match", "precondition_failed", 422, {
        estimated_cost_usd: 0.1,
        confirm_cost_usd: 0.09,
      }),
    );
    // confirmCostUsd is set → needs_confirm path is gated off
    const result = await probeRegenerate("proj_1", 2, {
      confirmCostUsd: 0.09,
    });
    expect(result.status).toBe("error");
  });
});

describe("hasEstimatedCost / exportBlockers", () => {
  it("reads estimated_cost_usd only when numeric", () => {
    expect(hasEstimatedCost({ estimated_cost_usd: 0.02 })).toBe(true);
    expect(hasEstimatedCost({ estimated_cost_usd: "0.02" })).toBe(false);
    expect(hasEstimatedCost({})).toBe(false);
  });

  it("normalises blockers to strings", () => {
    expect(exportBlockers({ blockers: ["true_peak", "loudness"] })).toEqual([
      "true_peak",
      "loudness",
    ]);
    expect(exportBlockers({})).toEqual([]);
    expect(exportBlockers({ blockers: "nope" })).toEqual([]);
  });
});

describe("tryExport — blocker listing", () => {
  it("returns blocked with the API's blocker list", async () => {
    vi.spyOn(projects, "createExport").mockRejectedValueOnce(
      new ApiError("QA blocks this export", "precondition_failed", 422, {
        blockers: ["true_peak_over"],
        override: "set override_blocking to publish anyway",
      }),
    );
    const result = await tryExport("proj_1");
    expect(result).toEqual({
      status: "blocked",
      blockers: ["true_peak_over"],
      message: "QA blocks this export",
    });
  });

  it("still offers override when blockers list is missing", async () => {
    vi.spyOn(projects, "createExport").mockRejectedValueOnce(
      new ApiError("blocked", "precondition_failed", 422, {}),
    );
    const result = await tryExport("proj_1");
    expect(result.status).toBe("blocked");
    if (result.status !== "blocked") return;
    expect(result.blockers.length).toBeGreaterThan(0);
  });

  it("accepts an explicit override", async () => {
    const accepted = {
      run_id: "run_exp",
      kind: "export",
      status: "running",
    };
    const spy = vi
      .spyOn(projects, "createExport")
      .mockResolvedValueOnce(accepted);
    const result = await tryExport("proj_1", { override_blocking: true });
    expect(result).toEqual({ status: "accepted", accepted });
    expect(spy).toHaveBeenCalledWith("proj_1", { override_blocking: true });
  });
});
