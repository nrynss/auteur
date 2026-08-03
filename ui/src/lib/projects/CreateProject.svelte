<script lang="ts">
  /** Create project → direct upload with real progress → confirm → analyze.
   * docs/api.md §3.2 / §3.3. Honest failure surfaces; never claims analysis
   * finished until a run is accepted. Cancel never orphans silently. */
  import Button from "../components/Button.svelte";
  import Panel from "../components/Panel.svelte";
  import Spinner from "../components/Spinner.svelte";
  import ThemeToggle from "../components/ThemeToggle.svelte";
  import { BRAND_NAME } from "../brand";
  import HelpHint from "../help/HelpHint.svelte";
  import { ApiError } from "../api/client";
  import {
    analyzeProject,
    confirmUpload,
    createProject,
    type CreateProjectResponse,
  } from "../api/projects";
  import { formatCost } from "../format";
  import { buildCreatePayload } from "./create-payload";
  import { waitForRun } from "./poll-run";
  import {
    formatUploadProgress,
    putWithProgress,
    UploadError,
    type UploadProgress,
  } from "./upload";

  interface Props {
    onNavigate?: (path: string) => void;
  }

  let { onNavigate }: Props = $props();

  type Phase =
    | "form"
    | "creating"
    | "uploading"
    | "confirming"
    | "ingesting"
    | "analyzing"
    | "done"
    | "error";

  let title = $state("");
  // Keep as string — type=text avoids Svelte 5 number coercion vs .trim().
  let budgetUsd = $state("");
  let file = $state<File | null>(null);
  let phase = $state<Phase>("form");
  let errorMessage = $state("");
  let errorCode = $state("");
  let progress = $state<UploadProgress | null>(null);
  let projectId = $state<string | null>(null);
  let projectTitle = $state("");
  let ingestRunId = $state<string | null>(null);
  let analyzeRunId = $state<string | null>(null);
  let analyzeEstimate = $state<number | null>(null);
  let phaseDetail = $state("");
  /** Full create response kept so a failed PUT can retry the same project. */
  let createdResponse = $state<CreateProjectResponse | null>(null);
  let canRetryUpload = $state(false);

  let abort: AbortController | null = null;
  /** True once cancel() has been pressed for this run. */
  let userCancelled = $state(false);

  const busy = $derived(
    phase === "creating" ||
      phase === "uploading" ||
      phase === "confirming" ||
      phase === "ingesting" ||
      phase === "analyzing",
  );

  function go(path: string) {
    if (onNavigate) onNavigate(path);
    else window.location.assign(path);
  }

  function onFileChange(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    file = input.files?.[0] ?? null;
  }

  function fail(message: string, code = "", opts: { retryUpload?: boolean } = {}) {
    phase = "error";
    errorMessage = message;
    errorCode = code;
    phaseDetail = "";
    canRetryUpload =
      !!opts.retryUpload && !!projectId && !!createdResponse && !!file;
  }

  function apiFail(e: unknown, fallback: string, opts: { retryUpload?: boolean } = {}) {
    if (userCancelled || (e instanceof DOMException && e.name === "AbortError")) {
      cancelledSurface();
      return;
    }
    if (e instanceof ApiError) {
      fail(e.message, e.code, opts);
      return;
    }
    if (e instanceof UploadError) {
      fail(
        e.message,
        e.status != null ? `http_${e.status}` : "upload_error",
        opts,
      );
      return;
    }
    fail(e instanceof Error ? e.message : fallback, "", opts);
  }

  function cancelledSurface() {
    phase = "error";
    errorCode = "cancelled";
    phaseDetail = "";
    canRetryUpload = !!projectId && !!createdResponse && !!file;
    if (projectId) {
      errorMessage =
        "Cancelled. The project record exists. Open its control room or retry the upload if the source never landed.";
    } else {
      errorMessage = "Cancelled.";
    }
  }

  function resetRunState() {
    errorMessage = "";
    errorCode = "";
    progress = null;
    ingestRunId = null;
    analyzeRunId = null;
    analyzeEstimate = null;
    canRetryUpload = false;
    userCancelled = false;
  }

  async function runUploadPipeline(
    created: CreateProjectResponse,
    sourceFile: File,
    signal: AbortSignal,
  ) {
    // 2. Direct PUT with real progress
    phase = "uploading";
    phaseDetail = "Uploading source video…";
    progress = { loaded: 0, total: sourceFile.size, fraction: 0 };
    try {
      await putWithProgress(created.upload.url, sourceFile, {
        headers: created.upload.headers ?? {},
        signal,
        onProgress: (p) => {
          progress = p;
        },
      });
    } catch (e) {
      apiFail(
        e,
        "Upload failed. The project record exists but has no source yet.",
        { retryUpload: true },
      );
      return false;
    }
    if (signal.aborted || userCancelled) {
      cancelledSurface();
      return false;
    }

    // 3. Confirm upload → ingest run
    phase = "confirming";
    phaseDetail = "Confirming upload…";
    let accepted;
    try {
      accepted = await confirmUpload(created.project.id, { signal });
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.code === "precondition_failed") {
          fail(
            "Upload confirmation failed. The object was not found in storage. " +
              e.message,
            e.code,
            { retryUpload: true },
          );
          return false;
        }
        if (e.code === "already_running") {
          fail(
            `An ingest is already running for this project (${String(e.detail?.run_id ?? "unknown run")}).`,
            e.code,
          );
          return false;
        }
      }
      apiFail(e, "Could not confirm upload.", { retryUpload: true });
      return false;
    }
    if (signal.aborted || userCancelled) {
      cancelledSurface();
      return false;
    }

    ingestRunId = accepted.run_id;

    // 4. Wait for ingest — analysis needs source_sha256
    phase = "ingesting";
    phaseDetail = `Ingest running (${accepted.run_id})…`;
    try {
      const ingest = await waitForRun(created.project.id, accepted.run_id, {
        signal,
        onTick: (run) => {
          phaseDetail = run.current_stage
            ? `Ingest · ${run.current_stage}`
            : `Ingest · ${run.status}`;
        },
        onTransientError: (_e, n) => {
          // Say it out loud rather than looking frozen — the run is still
          // going on the server; this is only the poll failing.
          phaseDetail = `Ingest · lost contact, retrying (${n})…`;
        },
      });
      if (ingest.status === "failed" || ingest.status === "stale") {
        fail(
          ingest.error
            ? `Ingest ${ingest.status}: ${ingest.error}`
            : `Ingest ${ingest.status}. Open the control room for run history.`,
          ingest.error_code ?? ingest.status,
        );
        return false;
      }
    } catch (e) {
      apiFail(e, "Timed out waiting for ingest.");
      return false;
    }
    if (signal.aborted || userCancelled) {
      cancelledSurface();
      return false;
    }

    // 5. Start analysis (does not wait for it to finish)
    phase = "analyzing";
    phaseDetail = "Starting analysis…";
    try {
      const analysis = await analyzeProject(created.project.id, {}, { signal });
      if (signal.aborted || userCancelled) {
        cancelledSurface();
        return false;
      }
      analyzeRunId = analysis.run_id;
      analyzeEstimate =
        analysis.estimated_cost_usd != null
          ? analysis.estimated_cost_usd
          : null;
      phase = "done";
      phaseDetail = "Analysis accepted. Opening control room.";
      await sleep(600, signal);
      if (signal.aborted || userCancelled) {
        cancelledSurface();
        return false;
      }
      go(`/projects/${encodeURIComponent(created.project.id)}`);
      return true;
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.code === "already_running") {
          if (signal.aborted || userCancelled) {
            cancelledSurface();
            return false;
          }
          phase = "done";
          phaseDetail =
            "A run is already in flight for this project. Opening the control room.";
          await sleep(600, signal);
          if (signal.aborted || userCancelled) {
            cancelledSurface();
            return false;
          }
          go(`/projects/${encodeURIComponent(created.project.id)}`);
          return true;
        }
        if (e.code === "precondition_failed") {
          fail(
            "Analysis refused. Upload is not fully ingested yet. " + e.message,
            e.code,
          );
          return false;
        }
        if (e.code === "quota_exhausted") {
          fail(
            "The model’s daily request limit is spent. The project is uploaded; open the control room and retry later.",
            e.code,
          );
          return false;
        }
        if (e.code === "storage_unavailable") {
          fail(
            e.message +
              " Direct upload requires the B2 backend (LocalBackend cannot presign).",
            e.code,
          );
          return false;
        }
      }
      apiFail(e, "Could not start analysis.");
      return false;
    }
  }

  function sleep(ms: number, signal?: AbortSignal): Promise<void> {
    return new Promise((resolve, reject) => {
      if (signal?.aborted) {
        reject(new DOMException("Aborted", "AbortError"));
        return;
      }
      const t = setTimeout(() => {
        signal?.removeEventListener("abort", onAbort);
        resolve();
      }, ms);
      const onAbort = () => {
        clearTimeout(t);
        reject(new DOMException("Aborted", "AbortError"));
      };
      signal?.addEventListener("abort", onAbort, { once: true });
    });
  }

  async function submit(event: Event) {
    event.preventDefault();
    if (busy) return;
    if (!file) {
      fail("Choose a video file to upload.");
      return;
    }

    const shaped = buildCreatePayload({
      title,
      budgetUsd,
      file,
    });
    if (!shaped.ok) {
      fail(shaped.error);
      return;
    }

    abort?.abort();
    abort = new AbortController();
    const signal = abort.signal;
    const sourceFile = file;

    resetRunState();
    projectId = null;
    projectTitle = "";
    createdResponse = null;

    // 1. Create project + upload target
    phase = "creating";
    phaseDetail = "Requesting upload target…";
    let created: CreateProjectResponse;
    try {
      created = await createProject(shaped.body, { signal });
    } catch (e) {
      if (e instanceof ApiError && e.code === "storage_unavailable") {
        fail(
          e.message +
            " Direct upload requires the B2 backend (set FOLEYFLOW_CACHE_BACKEND=b2).",
          e.code,
        );
        return;
      }
      apiFail(e, "Could not create project.");
      return;
    }

    // Assign id immediately so cancel never orphans silently.
    projectId = created.project.id;
    projectTitle = created.project.title;
    createdResponse = created;

    if (signal.aborted || userCancelled) {
      cancelledSurface();
      return;
    }

    await runUploadPipeline(created, sourceFile, signal);
  }

  /** Retry PUT using the stored upload target (same project). */
  async function retryUpload() {
    if (!createdResponse || !file || busy) return;
    abort?.abort();
    abort = new AbortController();
    const signal = abort.signal;
    userCancelled = false;
    resetRunState();
    await runUploadPipeline(createdResponse, file, signal);
  }

  function cancel() {
    userCancelled = true;
    abort?.abort();
    // If create already returned an id, always surface it — never silent home.
    if (projectId) {
      cancelledSurface();
    } else if (phase === "creating") {
      // Create may still be in flight; when it resolves, cancelledSurface runs.
      phase = "error";
      errorMessage = "Cancelling…";
      errorCode = "cancelled";
    } else {
      go("/");
    }
  }

  function backToForm() {
    phase = "form";
    errorMessage = "";
    errorCode = "";
    projectId = null;
    projectTitle = "";
    createdResponse = null;
    canRetryUpload = false;
    progress = null;
    phaseDetail = "";
    ingestRunId = null;
    analyzeRunId = null;
    analyzeEstimate = null;
  }
</script>

<header class="chrome">
  <a
    class="brand"
    href="/"
    onclick={(e) => {
      e.preventDefault();
      go("/");
    }}>{BRAND_NAME}</a
  >
  <span class="dim">new project</span>
  <span class="spacer"></span>
  <HelpHint />
  <ThemeToggle />
</header>

<main>
  <div class="head">
    <h1>New project</h1>
    <p class="lede dim">
      Title and source video. Bytes go straight to storage. Never through the API.
    </p>
  </div>

  <Panel class="form-panel">
    <form class="form" onsubmit={submit}>
      <label class="field">
        <span class="label">Title</span>
        <input
          type="text"
          name="title"
          bind:value={title}
          maxlength={200}
          required
          disabled={busy}
          placeholder="Western standoff"
          autocomplete="off"
        />
      </label>

      <label class="field">
        <span class="label">
          Budget
          <span class="opt">optional · advisory USD</span>
        </span>
        <input
          type="text"
          name="budget"
          bind:value={budgetUsd}
          disabled={busy}
          placeholder="Default from providers.toml"
          inputmode="decimal"
          autocomplete="off"
        />
      </label>

      <label class="field">
        <span class="label">Source video</span>
        <input
          type="file"
          name="file"
          accept="video/mp4,video/quicktime,video/webm,video/x-matroska,.mp4,.mov,.webm,.mkv,.m4v"
          disabled={busy}
          onchange={onFileChange}
          required
        />
        {#if file}
          <span class="file-meta num faint">
            {file.name}
            · {(file.size / (1024 * 1024)).toFixed(1)} MB
            · {file.type || "type unknown"}
          </span>
        {/if}
      </label>

      {#if phase !== "form" && phase !== "error"}
        <div class="progress" role="status" aria-live="polite">
          <div class="progress-head">
            {#if busy}
              <Spinner size={12} label={phase} />
            {/if}
            <span class="phase-label">{phaseDetail || phase}</span>
          </div>
          {#if phase === "uploading" && progress}
            <div class="bar" aria-hidden="true">
              <i style={`width: ${Math.round(progress.fraction * 100)}%`}></i>
            </div>
            <span class="num faint">{formatUploadProgress(progress)}</span>
          {/if}
          {#if projectId}
            <span class="id num faint">project {projectId}</span>
          {/if}
          {#if analyzeRunId}
            <span class="id num faint">
              analyze {analyzeRunId}
              {#if analyzeEstimate != null}
                · est. {formatCost(analyzeEstimate)}
              {/if}
            </span>
          {/if}
        </div>
      {/if}

      {#if phase === "error"}
        <div class="error" role="alert">
          <p class="stop">{errorMessage}</p>
          {#if errorCode}
            <p class="faint num">code {errorCode}</p>
          {/if}
          {#if projectId}
            <p class="dim">
              Project <span class="num">{projectId}</span>
              {#if projectTitle}
                ({projectTitle})
              {/if}
              was created.
              {#if canRetryUpload}
                Retry the upload to the same project, or open its control room
                (the room does not re-issue a fresh upload URL).
              {:else}
                Open its control room for status.
              {/if}
            </p>
          {/if}
        </div>
      {/if}

      <div class="actions">
        {#if phase === "done"}
          <Button
            variant="primary"
            type="button"
            onclick={() =>
              projectId && go(`/projects/${encodeURIComponent(projectId)}`)}
          >
            Open control room
          </Button>
        {:else if phase === "error"}
          {#if canRetryUpload}
            <Button variant="primary" type="button" onclick={retryUpload}>
              Retry upload
            </Button>
          {/if}
          {#if projectId}
            <Button
              type="button"
              variant={canRetryUpload ? "secondary" : "primary"}
              onclick={() => go(`/projects/${encodeURIComponent(projectId!)}`)}
            >
              Open control room
            </Button>
          {/if}
          <Button type="button" onclick={backToForm}>Back to form</Button>
        {:else}
          <Button variant="primary" type="submit" pending={busy} disabled={busy || !file}>
            {phase === "form" ? "Create & upload" : "Working…"}
          </Button>
          {#if busy}
            <Button type="button" variant="ghost" onclick={cancel}>Cancel</Button>
          {:else}
            <Button type="button" variant="ghost" onclick={() => go("/")}>
              Cancel
            </Button>
          {/if}
        {/if}
      </div>
    </form>
  </Panel>

  <p class="foot faint">
    After upload confirm, ingest hashes the source; analysis is started only once
    that finishes. The control room opens when analysis is <em>accepted</em>, not
    when it completes.
  </p>
  <p class="foot faint">
    <strong>Creating spends.</strong> Analysis is a billable vision-model call
    against a daily request limit, started automatically once ingest finishes
    (SPEC §6.2). Upload and ingest themselves cost nothing. The run's own
    estimate is shown when the API accepts it, and every charge is recorded
    against this project’s spend.
  </p>
</main>

<style>
  .chrome {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    height: var(--chrome-h);
    padding: 0 1rem;
    background: var(--surface);
    border-bottom: 1px solid var(--line);
  }

  .brand {
    font-weight: var(--w-590);
    font-size: var(--text-14);
    letter-spacing: 0.01em;
    color: var(--text);
    text-decoration: none;
  }

  .brand:hover {
    color: var(--accent);
  }

  .spacer {
    flex: 1;
  }

  main {
    max-width: 520px;
    margin: 0 auto;
    padding: 1.25rem 1rem 2rem;
  }

  .head {
    margin-bottom: 0.85rem;
  }

  h1 {
    font-size: var(--text-14);
    font-weight: var(--w-590);
    margin: 0 0 0.25rem;
  }

  .lede {
    margin: 0;
    font-size: var(--text-12);
  }

  :global(.form-panel) {
    padding: 1rem 1rem 1.1rem;
  }

  .form {
    display: flex;
    flex-direction: column;
    gap: 0.85rem;
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
  }

  .label {
    font-size: var(--text-10);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--faint);
    font-weight: var(--w-560);
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
  }

  .opt {
    text-transform: none;
    letter-spacing: 0;
    font-weight: var(--w-500);
    color: var(--faint);
    font-size: var(--text-11);
  }

  input[type="text"] {
    font: inherit;
    color: var(--text);
    background: var(--raised);
    border: 1px solid var(--line);
    border-radius: var(--r-sm);
    padding: 0.45rem 0.6rem;
  }

  input[type="text"]:focus,
  input[type="file"]:focus {
    outline: var(--focus-ring);
    outline-offset: 2px;
  }

  input:disabled {
    opacity: 0.55;
  }

  input[type="file"] {
    font: inherit;
    font-size: var(--text-12);
    color: var(--dim);
  }

  .file-meta {
    font-size: var(--text-11);
  }

  .progress {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    padding: 0.65rem 0.75rem;
    background: var(--raised);
    border: 1px solid var(--line-soft);
    border-radius: var(--r-sm);
  }

  .progress-head {
    display: flex;
    align-items: center;
    gap: 0.45rem;
  }

  .phase-label {
    font-size: var(--text-12);
    color: var(--dim);
  }

  .bar {
    height: 4px;
    border-radius: 2px;
    background: var(--sunken);
    overflow: hidden;
  }

  .bar > i {
    display: block;
    height: 100%;
    background: var(--accent);
  }

  .error {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    padding: 0.65rem 0.75rem;
    border: 1px solid var(--stop-q);
    background: var(--stop-q);
    border-radius: var(--r-sm);
  }

  .error p {
    margin: 0;
    font-size: var(--text-12);
  }

  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    padding-top: 0.25rem;
  }

  .foot {
    margin: 0.85rem 0 0;
    font-size: var(--text-11);
    line-height: 1.45;
  }

  .num {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }

  .id {
    font-size: var(--text-10);
  }

  .dim {
    color: var(--dim);
  }

  .faint {
    color: var(--faint);
  }

  .stop {
    color: var(--stop);
  }
</style>
