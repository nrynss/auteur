/** Direct-to-storage PUT with real upload progress (docs/api.md §3.2).
 * Uses XHR because fetch has no upload progress without ReadableStream
 * body hacks that browsers still treat unevenly. */

export class UploadError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = "UploadError";
    this.status = status;
  }
}

export interface UploadProgress {
  /** Bytes sent so far. */
  loaded: number;
  /** Total bytes (file size); 0 if the browser cannot report it. */
  total: number;
  /** 0–1 fraction; 0 when total is unknown. */
  fraction: number;
}

export interface PutUploadOptions {
  /** Required headers from the upload target (e.g. Content-Type). */
  headers?: Record<string, string>;
  onProgress?: (p: UploadProgress) => void;
  signal?: AbortSignal;
}

/** PUT `body` to a presigned (or local) URL. Resolves on 2xx only. */
export function putWithProgress(
  url: string,
  body: Blob,
  opts: PutUploadOptions = {},
): Promise<void> {
  const { headers = {}, onProgress, signal } = opts;

  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new UploadError("Upload aborted"));
      return;
    }

    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);

    for (const [key, value] of Object.entries(headers)) {
      if (value != null && value !== "") xhr.setRequestHeader(key, value);
    }

    xhr.upload.onprogress = (event) => {
      if (!onProgress) return;
      const total = event.lengthComputable ? event.total : body.size || 0;
      const loaded = event.loaded;
      onProgress({
        loaded,
        total,
        fraction: total > 0 ? Math.min(1, loaded / total) : 0,
      });
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress?.({ loaded: body.size, total: body.size, fraction: 1 });
        resolve();
        return;
      }
      reject(
        new UploadError(
          `Upload failed (${xhr.status} ${xhr.statusText || "error"})`,
          xhr.status,
        ),
      );
    };

    xhr.onerror = () => {
      reject(new UploadError("Upload failed — network error", null));
    };

    xhr.onabort = () => {
      reject(new UploadError("Upload aborted", null));
    };

    const onAbort = () => xhr.abort();
    signal?.addEventListener("abort", onAbort, { once: true });

    xhr.addEventListener("loadend", () => {
      signal?.removeEventListener("abort", onAbort);
    });

    xhr.send(body);
  });
}

/** Format a progress line for the create form ("12.4 / 48.0 MB · 26%"). */
export function formatUploadProgress(p: UploadProgress): string {
  if (p.total <= 0) {
    return `${formatBytes(p.loaded)} uploaded`;
  }
  const pct = Math.round(p.fraction * 100);
  return `${formatBytes(p.loaded)} / ${formatBytes(p.total)} · ${pct}%`;
}

function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
