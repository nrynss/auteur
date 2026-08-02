/** The documented error envelope (docs/api.md §2). FastAPI inlines it in
 * route responses rather than referencing a shared component, so it is not
 * present in the generated schema — keep it here next to the code that
 * unwraps it. */
export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    detail: Record<string, unknown>;
  };
}

/** A request that reached the API but was refused — carries the contract's
 * stable `code` (docs/api.md §2). UI branches on `code`, never on `message`. */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly detail: Record<string, unknown>;

  constructor(
    message: string,
    code: string,
    status: number,
    detail: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.detail = detail;
  }
}

/** Fetch wrapper against the one error shape the API guarantees:
 * `{"error": {"code", "message", "detail"}}` on every non-2xx response.
 * Anything that throws here is already a human-readable message. */
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const error = (body as ErrorEnvelope | null)?.error;
    throw new ApiError(
      error?.message ?? `${response.status} ${response.statusText}`,
      error?.code ?? "http_error",
      response.status,
      error?.detail ?? {},
    );
  }
  return body as T;
}
