#!/usr/bin/env node
// Gera a configuração do nginx do site a partir dos modelos, no BUILD da imagem (site/Dockerfile) e nos testes
// (infra/tests/test_security_headers.py):
//   node scripts/render-nginx.mjs --dist dist --connect-src https://app.factoryconnect.tech --out out/
//   → out/default.conf (de nginx.conf) e out/security-headers.conf (de nginx/security-headers.conf)
// Placeholders: __CSP_SCRIPT_HASHES__ = 'sha256-…' de cada <script> inline EXECUTÁVEL do dist (tema antes da
// pintura, scripts do Astro embutidos); __CONNECT_SRC__ = origem da API (formulário de contato).
// Assim o CSP do site não precisa de 'unsafe-inline' em script-src, e um script inline novo sem hash não roda.
// Saída também: --print-hashes só imprime os hashes.
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const site = resolve(here, "..");

function arg(name, def) {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 ? process.argv[i + 1] : def;
}

function htmlFiles(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...htmlFiles(p));
    else if (name.endsWith(".html")) out.push(p);
  }
  return out;
}

// Tipos que o navegador NÃO executa (dados): o CSP não se aplica a eles.
const DATA_TYPES = /^(application\/(ld\+)?json|text\/template|text\/x-template)$/i;

export function inlineScriptHashes(dist) {
  const hashes = new Set();
  const re = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
  for (const file of htmlFiles(dist)) {
    const html = readFileSync(file, "utf8");
    for (const m of html.matchAll(re)) {
      const attrs = m[1];
      if (/\bsrc\s*=/i.test(attrs)) continue;
      const type = (attrs.match(/\btype\s*=\s*["']?([^"'\s>]+)/i) || [])[1] || "";
      if (DATA_TYPES.test(type)) continue;
      hashes.add(`'sha256-${createHash("sha256").update(m[2], "utf8").digest("base64")}'`);
    }
  }
  return [...hashes].sort();
}

const dist = resolve(arg("dist", join(site, "dist")));
const hashes = inlineScriptHashes(dist);
if (process.argv.includes("--print-hashes")) {
  console.log(hashes.join(" "));
  process.exit(0);
}
const connect = arg("connect-src", "https://app.factoryconnect.tech");
if (!/^https?:\/\/[A-Za-z0-9.:-]+$/.test(connect)) {
  console.error(`render-nginx: --connect-src inválido: ${connect}`);
  process.exit(2);
}
const outDir = resolve(arg("out", join(site, "nginx-out")));
mkdirSync(outDir, { recursive: true });
const fill = (s) => s.replaceAll("__CSP_SCRIPT_HASHES__", hashes.join(" ")).replaceAll("__CONNECT_SRC__", connect);
writeFileSync(join(outDir, "default.conf"), fill(readFileSync(join(site, "nginx.conf"), "utf8")));
writeFileSync(join(outDir, "security-headers.conf"), fill(readFileSync(join(site, "nginx/security-headers.conf"), "utf8")));
console.log(`render-nginx: ${hashes.length} script(s) inline com hash → ${outDir}`);
