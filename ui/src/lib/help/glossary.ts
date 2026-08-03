/** Glossary — the product's defined terms (handoff.md task 22).
 *
 * One module holding each term's label and one-line definition, consumed by
 * the tooltips, the help overlay and (via those) every place a term renders.
 * A term cannot be defined two ways because there is one source.
 *
 * The register rule from the brief: craft terms the audience knows cold
 * (stem, mix, transient) stay without apology; terms that are ours and
 * defined nowhere (readiness, dossier, unclaimed) stay because a one-line
 * definition earns them; engineering terms that name an internal mechanism
 * rather than a thing the user has (ledger, quota) are renamed in
 * director-facing copy to the thing the user actually has — costs,
 * transactions, usage — and kept only on the operator surface (the config
 * screen and its doctor-parity rows), where they are defined here too.
 *
 * Every key below must appear in the rendered UI (see help.test.ts, which
 * fails when a defined term is never rendered or a rendered dense term is
 * undefined).
 */

export interface TermDef {
  /** The term as rendered. */
  label: string;
  /** One-line definition — the evidence-first voice: what it is, never a
   * judgement. */
  def: string;
}

export const GLOSSARY: Record<string, TermDef> = {
  // -- craft terms: the audience knows these cold ---------------------------
  stem: {
    label: "stem",
    def: "the single recorded sound for one event; events that route to the same request share one stem",
  },
  mix: {
    label: "mix",
    def: "the finished audio track assembled from the timeline, its placed stems and the ambience",
  },
  transient: {
    label: "transient",
    def: "the onset of a one-shot sound — the hit point the playhead marks, not the event's start",
  },
  // -- our terms, kept because a line defines them --------------------------
  readiness: {
    label: "readiness",
    def: "the checklist of what stands between this project and a finished mix",
  },
  dossier: {
    label: "dossier",
    def: "every artifact this project has produced, listed in one place",
  },
  unclaimed: {
    label: "unclaimed",
    def: "material the system owns — money spent, storage used — that no project claims",
  },
  continuity: {
    label: "continuity",
    def: "an adjacent-shot comparison that flags a visual jump for your judgement, never a verdict",
  },
  envelope: {
    label: "envelope",
    def: "the shape of a sound's attack and decay, drawn on each timeline block",
  },
  // -- editing vocabulary ---------------------------------------------------
  draft: {
    label: "draft",
    def: "your uncommitted working copy of the timeline; commit writes it as the next version",
  },
  version: {
    label: "version",
    def: "one immutable state of the timeline; switching versions is undo",
  },
  commit: {
    label: "commit",
    def: "write the draft as one new immutable version beside the others",
  },
  freeze: {
    label: "freeze",
    def: "a recorded value that locks a generated sound's address or texture, so an edit cannot silently re-roll it",
  },
  // -- money, in the director's words --------------------------------------
  spend: {
    label: "spend",
    def: "what this project has cost so far, from its recorded transactions",
  },
  transactions: {
    label: "transactions",
    def: "the recorded record of each provider call and what it cost — the audit trail behind every spend figure",
  },
  usage: {
    label: "usage",
    def: "how many calls a model allows in a day, counted from the recorded transactions",
  },
  // -- operator vocabulary (config screen + doctor-parity rows) -------------
  ledger: {
    label: "ledger",
    def: "the operator name for the transaction record every billed call is written to",
  },
  quota: {
    label: "quota",
    def: "the operator name for a model's per-day request limit",
  },
};

/** One-line definition for a term id, or null when the term is not defined
 * (a rendered dense term with no entry is a bug — see help.test.ts). */
export function termDef(id: string): TermDef | null {
  return GLOSSARY[id] ?? null;
}
