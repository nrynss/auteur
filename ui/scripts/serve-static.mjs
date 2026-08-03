/** Minimal SPA static server for the Playwright gate (task 22 + clipping
 * review). Serves the BUILT bundle from the package's static dir with an
 * index.html fallback for client-side routes — the same contract the API's
 * dashboard router provides in production. No dependencies. */
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize, sep } from "node:path";

const ROOT = join(import.meta.dirname, "..", "..", "src", "foleyflow", "api", "static");
const PORT = Number(process.env.PORT || 4173);

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript",
  ".mjs": "text/javascript",
  ".css": "text/css",
  ".svg": "image/svg+xml",
  ".json": "application/json",
  ".png": "image/png",
  ".ico": "image/x-icon",
};

createServer(async (req, res) => {
  try {
    const urlPath = decodeURIComponent(new URL(req.url ?? "/", "http://x").pathname);
    let filePath = join(ROOT, normalize(urlPath));
    if (filePath !== ROOT && !filePath.startsWith(ROOT + sep)) {
      res.writeHead(403);
      return res.end("forbidden");
    }
    let body;
    try {
      body = await readFile(filePath);
    } catch {
      // SPA fallback — client-side routes deep-link to index.html.
      filePath = join(ROOT, "index.html");
      body = await readFile(filePath);
    }
    res.writeHead(200, { "content-type": TYPES[extname(filePath)] ?? "application/octet-stream" });
    res.end(body);
  } catch {
    res.writeHead(500);
    res.end("internal error");
  }
}).listen(PORT, () => {
  console.log(`serving ${ROOT} on :${PORT}`);
});
