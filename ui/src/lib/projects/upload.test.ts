import { afterEach, describe, expect, it, vi } from "vitest";

import {
  formatUploadProgress,
  putWithProgress,
  UploadError,
  type UploadProgress,
} from "./upload";

describe("formatUploadProgress", () => {
  it("shows bytes and percent when total is known", () => {
    expect(
      formatUploadProgress({ loaded: 12.4 * 1024 * 1024, total: 48 * 1024 * 1024, fraction: 12.4 / 48 }),
    ).toMatch(/12\.4 MB \/ 48\.0 MB · 26%/);
  });

  it("falls back when total is unknown", () => {
    expect(
      formatUploadProgress({ loaded: 2048, total: 0, fraction: 0 }),
    ).toBe("2.0 KB uploaded");
  });
});

describe("putWithProgress", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("PUTs with required headers and reports progress", async () => {
    const progress: UploadProgress[] = [];
    let sentBody: Document | XMLHttpRequestBodyInit | null | undefined;
    let method = "";
    let url = "";
    const headers: Record<string, string> = {};

    class FakeXHR {
      status = 200;
      statusText = "OK";
      upload = {
        onprogress: null as ((e: ProgressEvent) => void) | null,
      };
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      onabort: (() => void) | null = null;
      open(m: string, u: string) {
        method = m;
        url = u;
      }
      setRequestHeader(k: string, v: string) {
        headers[k] = v;
      }
      addEventListener(_type: string, _fn: () => void) {}
      send(body?: Document | XMLHttpRequestBodyInit | null) {
        sentBody = body;
        this.upload.onprogress?.({
          lengthComputable: true,
          loaded: 50,
          total: 100,
        } as ProgressEvent);
        this.upload.onprogress?.({
          lengthComputable: true,
          loaded: 100,
          total: 100,
        } as ProgressEvent);
        queueMicrotask(() => this.onload?.());
      }
      abort() {
        this.onabort?.();
      }
    }

    vi.stubGlobal("XMLHttpRequest", FakeXHR as unknown as typeof XMLHttpRequest);

    const blob = new Blob(["hello-video"], { type: "video/mp4" });
    await putWithProgress("https://storage.example/put", blob, {
      headers: { "Content-Type": "video/mp4", "x-amz-acl": "private" },
      onProgress: (p) => progress.push(p),
    });

    expect(method).toBe("PUT");
    expect(url).toBe("https://storage.example/put");
    expect(headers["Content-Type"]).toBe("video/mp4");
    expect(headers["x-amz-acl"]).toBe("private");
    expect(sentBody).toBe(blob);
    expect(progress.length).toBeGreaterThanOrEqual(2);
    expect(progress[0].fraction).toBe(0.5);
    expect(progress.at(-1)?.fraction).toBe(1);
  });

  it("rejects non-2xx as UploadError", async () => {
    class FakeXHR {
      status = 403;
      statusText = "Forbidden";
      upload = { onprogress: null };
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      onabort: (() => void) | null = null;
      open() {}
      setRequestHeader() {}
      addEventListener() {}
      send() {
        queueMicrotask(() => this.onload?.());
      }
      abort() {}
    }
    vi.stubGlobal("XMLHttpRequest", FakeXHR as unknown as typeof XMLHttpRequest);

    await expect(
      putWithProgress("https://storage.example/put", new Blob(["x"]), {
        headers: { "Content-Type": "video/mp4" },
      }),
    ).rejects.toBeInstanceOf(UploadError);
  });

  it("aborts when the signal is already aborted", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(
      putWithProgress("https://storage.example/put", new Blob(["x"]), {
        signal: controller.signal,
      }),
    ).rejects.toMatchObject({ name: "UploadError", message: "Upload aborted" });
  });
});
