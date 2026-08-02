/** Poll GET /runs/{id} until the run leaves "running" (docs/api.md §3.6). */

import { getRun, type RunOut } from "../api/projects";

export interface PollRunOptions {
  intervalMs?: number;
  timeoutMs?: number;
  signal?: AbortSignal;
  onTick?: (run: RunOut) => void;
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

  while (true) {
    if (opts.signal?.aborted) {
      throw new DOMException("Aborted", "AbortError");
    }
    const run = await getRun(projectId, runId);
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
