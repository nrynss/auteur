/** Poll GET /runs/{id} until the run leaves "running" (docs/api.md §3.6). */

import { ApiError } from "../api/client";
import { getRun, type RunOut } from "../api/projects";

export interface PollRunOptions {
  intervalMs?: number;
  timeoutMs?: number;
  signal?: AbortSignal;
  onTick?: (run: RunOut) => void;
  /** Consecutive transient fetch failures tolerated before giving up.
   * Ingest hashes the whole source and can run for minutes; one network blip
   * must not fail a create whose run is progressing fine on the server. */
  maxTransientErrors?: number;
  onTransientError?: (error: unknown, consecutive: number) => void;
}

export class PollTimeoutError extends Error {
  constructor(runId: string, timeoutMs: number) {
    super(`Run ${runId} still running after ${timeoutMs}ms`);
    this.name = "PollTimeoutError";
  }
}

/** Wait until status is not "running". Rejects on abort, timeout, or fetch error. */
export async function waitForRun(
  projectId: string,
  runId: string,
  opts: PollRunOptions = {},
): Promise<RunOut> {
  const intervalMs = opts.intervalMs ?? 1500;
  const timeoutMs = opts.timeoutMs ?? 15 * 60 * 1000;
  const started = Date.now();

  const maxTransient = opts.maxTransientErrors ?? 4;
  let consecutiveErrors = 0;

  while (true) {
    if (opts.signal?.aborted) {
      throw new DOMException("Aborted", "AbortError");
    }

    let run: RunOut;
    try {
      run = await getRun(projectId, runId);
      consecutiveErrors = 0;
    } catch (e) {
      // A 404 is terminal — the run id does not exist and never will. Any
      // other failure (network, 5xx) is transient: the run is still going on
      // the server, so retry rather than failing a create that is fine.
      const terminal = e instanceof ApiError && e.status === 404;
      consecutiveErrors += 1;
      if (terminal || consecutiveErrors > maxTransient) throw e;
      opts.onTransientError?.(e, consecutiveErrors);
      if (Date.now() - started > timeoutMs) {
        throw new PollTimeoutError(runId, timeoutMs);
      }
      await sleep(intervalMs, opts.signal);
      continue;
    }

    opts.onTick?.(run);
    if (run.status !== "running") return run;
    if (Date.now() - started > timeoutMs) {
      throw new PollTimeoutError(runId, timeoutMs);
    }
    await sleep(intervalMs, opts.signal);
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
