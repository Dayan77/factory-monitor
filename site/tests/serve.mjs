// Servidor estático mínimo que imita o nginx de produção (site/nginx.conf):
// /pagina → pagina.html, /pagina/ → 301 /pagina, inexistente → 404.html com status 404.
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { extname, join, normalize } from "node:path";

const TYPES = {
  ".html": "text/html; charset=utf-8", ".xml": "application/xml", ".txt": "text/plain; charset=utf-8",
  ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon", ".js": "text/javascript",
  ".css": "text/css", ".webmanifest": "application/manifest+json", ".json": "application/json",
};

async function isFile(p) {
  try { return (await stat(p)).isFile(); } catch { return false; }
}

export function serve(root, port) {
  const server = createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", "http://x");
    let path = decodeURIComponent(url.pathname);
    if (path.length > 1 && path.endsWith("/")) {
      res.writeHead(301, { Location: path.replace(/\/+$/, "") }); return res.end();
    }
    if (path === "/") path = "/index.html";
    const safe = normalize(path).replace(/^(\.\.[/\\])+/, "");
    for (const candidate of [safe, `${safe}.html`]) {
      const file = join(root, candidate);
      if (await isFile(file)) {
        res.writeHead(200, { "Content-Type": TYPES[extname(file)] ?? "application/octet-stream" });
        return res.end(await readFile(file));
      }
    }
    res.writeHead(404, { "Content-Type": TYPES[".html"] });
    res.end(await readFile(join(root, "404.html")));
  });
  return new Promise((resolve) => server.listen(port, "127.0.0.1", () => resolve(server)));
}
