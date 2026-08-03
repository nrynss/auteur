<script lang="ts">
  /** Director mode (unit 7.12, docs/api.md §3.16). Text in, timeline edits
   * out — and nothing is applied until the director says so. The chat is a
   * transcript of turns; the turn whose proposal is under review is the
   * *active* proposal, owned by ControlRoom so the timeline can render
   * ghosted old positions against new.
   *
   * Cost geometry is on screen: every send is one `gemini-3.5-flash-lite`
   * call through the ledger (billed, quota-counted), and the moves themselves
   * are free — the same cached stems placed elsewhere. When the ledger is
   * unwritable the call may still have billed the provider while the row did
   * not land, so the cost is presented as unrecorded (§3.10's fail-closed
   * rule), not as a clean figure.
   */
  import type { DirectorResponse } from "../api/projects";
  import Spinner from "../components/Spinner.svelte";
  import Tooltip from "../help/Tooltip.svelte";
  import { BRAND_NAME } from "../brand";

  /** The document a proposal was diffed against — what the director was
   * looking at when they asked. */
  export interface DirectorBase {
    version: number;
    duration: number;
    ambience: string;
    events: {
      index: number;
      name: string;
      kind: string;
      intensity: string;
      start_time: number;
      duration: number;
      seed?: number | null;
      source_duration_s?: number | null;
    }[];
  }

  export interface DirectorTurn {
    role: "user" | "assistant";
    text: string;
    /** The proposal a proposed-status turn carried; null for other turns. */
    proposal: DirectorResponse | null;
    base: DirectorBase | null;
    outcome: "reviewing" | "applied" | "discarded" | "stale" | "noted" | "failed";
  }

  interface Props {
    projectId: string;
    turns: DirectorTurn[];
    busy: boolean;
    /** A run in flight locks editing — Apply (a draft edit) is disabled. */
    locked: boolean;
    /** The proposal currently under review (the last reviewing turn's), or
     * null — drives the timeline ghosts via ControlRoom. */
    active: DirectorResponse | null;
    /** The document the director is looking at — draft or committed. Null
     * while viewing a historical version (propose from the draft view). */
    baseTimeline: DirectorBase | null;
    onSend: (instruction: string) => void;
    onApply: () => void;
    onDiscard: () => void;
  }

  let {
    projectId,
    turns,
    busy = false,
    locked = false,
    active,
    baseTimeline,
    onSend,
    onApply,
    onDiscard,
  }: Props = $props();

  let composer = $state("");

  const canSend = $derived(
    !busy && !active && baseTimeline != null && composer.trim().length > 0,
  );

  function modelName() {
    return turns.findLast((t) => t.role === "assistant")?.proposal?.model ??
      "gemini-3.5-flash-lite";
  }

  function send() {
    const instruction = composer.trim();
    if (!canSend) return;
    composer = "";
    onSend(instruction);
  }

  /** "start 11.000s → 10.920s · span 1.5s → 1.9s" for one edit. */
  function changedFields(e: DirectorResponse["edits"][number]): string[] {
    const parts: string[] = [];
    const moved = Math.abs(e.old.start_time - e.new.start_time) > 1e-9;
    const resized = Math.abs(e.old.duration - e.new.duration) > 1e-9;
    if (moved)
      parts.push(`${e.old.start_time.toFixed(3)}s → ${e.new.start_time.toFixed(3)}s`);
    if (resized)
      parts.push(`${e.old.duration.toFixed(3)}s → ${e.new.duration.toFixed(3)}s`);
    return parts;
  }

  function eventName(p: DirectorResponse, index: number) {
    return (
      p.timeline.events.find((e) => e.index === index)?.name ?? `event ${index}`
    );
  }

  const OUTCOME_LINES: Record<DirectorTurn["outcome"], string | null> = {
    reviewing: null,
    applied: "Applied to the draft. Ghosted blocks are gone; commit still writes one new version.",
    discarded: "Discarded. The timeline is untouched.",
    stale: "The timeline moved underneath. This proposal was cleared. Re-ask if you still want it.",
    noted: null,
    failed: null,
  };
</script>

<section class="director" aria-label="Director mode">
  {#if turns.length === 0}
    <p class="empty">
      Describe a change: "pull the gunshot back 80 ms", "move every footstep
      40 ms earlier". Each send is one {modelName()} call, recorded in the
      ledger; the proposal is never applied until you approve it.
    </p>
  {:else}
    <div class="chat">
      {#each turns as turn, i (i)}
        {#if turn.role === "user"}
          <div class="msg you">
            <span class="who">You</span>
            <div class="body">{turn.text}</div>
          </div>
        {:else if turn.proposal}
          <div class="msg">
            <span class="who">{BRAND_NAME} · {turn.proposal.model}</span>
            <div class="body">{turn.text}</div>
            {#if turn.outcome === "reviewing"}
              <div class="diff" role="region" aria-label="Proposed edit">
                <header>
                  <span>
                    {turn.proposal.edits.length}
                    {turn.proposal.edits.length === 1 ? "event" : "events"} ·
                    proposed by index
                  </span>
                  {#if turn.proposal.ledger_writable}
                    <span class="cost">call ~${turn.proposal.cost_usd.toFixed(4)} · moves free</span>
                  {:else}
                    <span class="cost unwritable">call billed but not recorded: ledger unwritable</span>
                  {/if}
                </header>
                <ul>
                  {#each turn.proposal.edits as edit (edit.index)}
                    <li>
                      <span class="name">
                        {eventName(turn.proposal, edit.index)}
                        <span class="idx num">idx {edit.index}</span>
                      </span>
                      <span class="was num">{changedFields(edit).join(" · ")}</span>
                    </li>
                  {/each}
                </ul>
                <footer>
                  <button
                    type="button"
                    class="btn primary"
                    disabled={locked}
                    onclick={onApply}
                  >
                    <Tooltip
                      text={locked
                        ? "A run is in flight. Apply to the draft when it settles"
                        : "Merge this proposal into the draft (an edit; commit still asks)"}
                    >
                      <span>Apply to draft</span>
                    </Tooltip>
                  </button>
                  <button type="button" class="btn ghost" onclick={onDiscard}>
                    Discard
                  </button>
                </footer>
              </div>
            {:else if OUTCOME_LINES[turn.outcome]}
              <p class="note" class:ok={turn.outcome === "applied"}>
                {OUTCOME_LINES[turn.outcome]}
              </p>
            {/if}
          </div>
        {:else}
          <div class="msg">
            <span class="who">{BRAND_NAME} · {modelName()}</span>
            <div class="body">{turn.text}</div>
          </div>
        {/if}
      {/each}
    </div>
  {/if}

  <div class="composer-wrap">
    {#if busy}
      <div class="busy">
        <Spinner label="Proposing" />
        <span class="dim">Proposing: one {modelName()} call through the ledger…</span>
      </div>
    {/if}
    <form
      class="composer"
      onsubmit={(e) => {
        e.preventDefault();
        send();
      }}
    >
      <input
        bind:value={composer}
        placeholder={active
          ? "Approve or discard the current proposal first"
          : baseTimeline == null
            ? "Return to the draft view to propose"
            : "Describe a change…"}
        aria-label="Describe a change"
        disabled={busy || !!active || baseTimeline == null}
      />
      <button
        type="submit"
        class="hint"
        disabled={!canSend}
      >
        <Tooltip text="Send: one billed director-model call">
          <span>⏎</span>
        </Tooltip>
      </button>
    </form>
    <p class="costline">
      One {modelName()} call per proposal, ledger-recorded and quota-counted.
      The moves themselves are free. The same cached stems, placed elsewhere.
    </p>
  </div>
</section>

<style>
  .director {
    display: flex;
    flex-direction: column;
    min-height: 0;
    gap: 10px;
  }

  .empty {
    margin: 0;
    padding: 10px 12px;
    border: 1px dashed var(--line);
    border-radius: var(--r);
    font-size: var(--text-12);
    line-height: 1.55;
    color: var(--dim);
  }

  .chat {
    display: flex;
    flex-direction: column;
    gap: 10px;
    min-height: 0;
    overflow-y: auto;
  }

  .msg {
    display: grid;
    gap: 4px;
  }

  .msg .who {
    font-size: 10px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--faint);
    font-weight: 600;
  }

  .msg .body {
    font-size: var(--text-13);
    line-height: 1.5;
    padding: 8px 10px;
    border-radius: var(--r);
    background: var(--raised);
    border: 1px solid var(--line-soft);
    white-space: pre-wrap;
  }

  .msg.you .body {
    background: var(--accent-q);
    border-color: transparent;
  }

  .diff {
    border: 1px solid var(--line);
    border-radius: var(--r);
    overflow: hidden;
  }

  .diff header {
    padding: 7px 10px;
    font-size: var(--text-11);
    color: var(--dim);
    border-bottom: 1px solid var(--line);
    background: var(--raised);
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
  }

  .diff header .cost {
    font: 600 11px/1 var(--font-mono);
    color: var(--ok);
    white-space: nowrap;
  }

  .diff header .cost.unwritable {
    color: var(--warn);
  }

  .diff ul {
    list-style: none;
    margin: 0;
    padding: 0;
  }

  .diff li {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 8px;
    align-items: baseline;
    padding: 6px 10px;
    border-bottom: 1px solid var(--line-soft);
    font-size: var(--text-12);
  }

  .diff li:last-child {
    border-bottom: 0;
  }

  .diff .name {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .diff .idx {
    color: var(--faint);
    font-size: 10px;
    margin-left: 6px;
  }

  .diff .was {
    font: 500 11px/1.4 var(--font-mono);
    color: var(--faint);
    text-align: right;
  }

  .diff footer {
    display: flex;
    gap: 6px;
    padding: 9px 10px;
    background: var(--raised);
  }

  .btn {
    font: inherit;
    font-size: var(--text-11);
    font-weight: var(--w-560);
    padding: 4px 10px;
    border-radius: var(--r);
    border: 1px solid var(--line);
    background: var(--surface);
    color: var(--dim);
    cursor: pointer;
  }

  .btn.primary {
    background: var(--accent);
    border-color: var(--accent);
    color: var(--surface);
  }

  .btn.ghost {
    background: transparent;
  }

  .btn:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }

  .note {
    margin: 0;
    font-size: var(--text-11);
    color: var(--dim);
    padding: 5px 10px;
  }

  .note.ok {
    color: var(--ok);
  }

  .composer-wrap {
    margin-top: auto;
    padding-top: 10px;
  }

  .busy {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 10px;
    font-size: var(--text-11);
    border: 1px solid var(--line-soft);
    border-radius: var(--r);
    margin-bottom: 6px;
  }

  .dim {
    color: var(--dim);
  }

  .composer {
    display: flex;
    gap: 6px;
    align-items: center;
    border: 1px solid var(--line);
    border-radius: var(--r);
    padding: 5px 5px 5px 10px;
    background: var(--raised);
  }

  .composer input {
    flex: 1 1 auto;
    border: 0;
    background: transparent;
    color: var(--text);
    font: inherit;
    min-width: 0;
  }

  .composer input:focus {
    outline: none;
  }

  .composer input:disabled {
    opacity: 0.6;
  }

  .composer .hint {
    font: 500 10px/1 var(--font-mono);
    color: var(--faint);
    border: 0;
    background: transparent;
    padding: 4px 8px;
    cursor: pointer;
    border-radius: 4px;
  }

  .composer .hint:hover:not(:disabled) {
    color: var(--text);
    background: var(--line-soft);
  }

  .composer .hint:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .costline {
    margin: 6px 2px 0;
    font-size: 10px;
    line-height: 1.5;
    color: var(--faint);
  }

  .num {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
</style>
