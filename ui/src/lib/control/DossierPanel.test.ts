/** DossierPanel's state matrix: the three defined states the handoff's
 * loading/empty/error rule demands, plus the data branch. The loading branch
 * says it is COMPOSING, the failure branch says it FAILED — and crucially a
 * failed fetch must not sit on "Composing…" forever (the old wiring left
 * `loading` true whenever the dossier was requested but not yet populated,
 * so a dead request read as work in progress indefinitely). */
import { expect, it } from "vitest";
import { render } from "svelte/server";

import DossierPanel from "./DossierPanel.svelte";

const DOSSIER = {
  project_id: "proj_TEST",
  artifact_count: 2,
  total_bytes: 1024 + 512,
  artifacts: [
    {
      kind: "mix",
      key: "projects/proj_TEST/runs/run_1/mix.wav",
      size_bytes: 1536,
      sha256: "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b",
    },
    {
      kind: "export",
      key: "exports/latest/mix.wav",
      size_bytes: null,
      sha256: null,
    },
  ],
};

it("loading reads as working, never as empty", () => {
  const { body } = render(DossierPanel, {
    props: { dossier: null, loading: true },
  });
  expect(body).toContain("Composing the dossier: one lookup per artifact…");
  expect(body).not.toContain("No artifacts yet");
});

it("a failed fetch says failure — not a forever 'Composing…' and not absence", () => {
  // The exact regression the wiring used to produce: request kicked off
  // (loading would be true) and the fetch died. The panel must render an
  // error, not claim to still be composing.
  const { body } = render(DossierPanel, {
    props: { dossier: null, loading: true, failed: true },
  });
  expect(body).toContain("Dossier could not be loaded.");
  expect(body).not.toContain("Composing the dossier");
  expect(body).not.toContain("No artifacts yet");
});

it("no dossier yet renders the empty state that names what fills it", () => {
  const { body } = render(DossierPanel, {
    props: { dossier: null, loading: false },
  });
  expect(body).toContain("No artifacts yet. Mix, repair or export creates them.");
});

it("a populated dossier renders every artifact row with its own size and sha", () => {
  const { body } = render(DossierPanel, {
    props: { dossier: DOSSIER as never, loading: false },
  });
  expect(body).toContain("2 artifacts");
  expect(body).toContain("1.5 KB"); // 1536 B → KB
  expect(body).toContain("mix");
  expect(body).toContain("export");
  // The sha is elided to ten chars, the long key elided to its ends.
  expect(body).toContain("a1b2c3d4e5");
  expect(body).toContain("projects/proj_TEST/runs/run_1/mix.wav"); // under 44 chars — whole, not elided
  // Absent size and sha render as "—", never a fabricated figure.
  expect(body).toContain("n/a");
});

it("a zero-artifact dossier is a real summary, not the empty state", () => {
  const { body } = render(DossierPanel, {
    props: {
      dossier: { project_id: "proj_TEST", artifact_count: 0, total_bytes: 0, artifacts: [] } as never,
      loading: false,
    },
  });
  expect(body).toContain("0 artifacts");
  expect(body).not.toContain("No artifacts yet");
});
