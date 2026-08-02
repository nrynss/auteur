import { describe, expect, it } from "vitest";

import { parseRoute } from "./route";

describe("parseRoute", () => {
  it("maps root and unknown paths to home", () => {
    expect(parseRoute("/")).toEqual({ name: "home" });
    expect(parseRoute("")).toEqual({ name: "home" });
    expect(parseRoute("/gallery")).toEqual({ name: "home" });
    expect(parseRoute("/projects")).toEqual({ name: "home" });
  });

  it("maps /projects/new to create (not a project id)", () => {
    expect(parseRoute("/projects/new")).toEqual({ name: "create" });
    expect(parseRoute("/projects/new/")).toEqual({ name: "create" });
  });

  it("maps /projects/{id} to the control room", () => {
    expect(parseRoute("/projects/proj_01KABC")).toEqual({
      name: "project",
      id: "proj_01KABC",
    });
    expect(parseRoute("/projects/proj%2Fweird")).toEqual({
      name: "project",
      id: "proj/weird",
    });
  });

  it("does not treat nested paths as project ids", () => {
    expect(parseRoute("/projects/proj_01/extra")).toEqual({ name: "home" });
  });
});
