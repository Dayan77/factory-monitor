/**
 * Sprint 7 — Lighthouse (mobile emulation, simulated throttling) of the PRODUCTION build: performance, accessibility,
 * best practices for /login, /dashboard, /op, /m (signed in through the real API; the token goes into
 * localStorage of the Chrome profile Lighthouse then audits with storage kept).
 *
 *   # production build served with gzip + /api proxied to a backend (nginx in prod; any static server here):
 *   BASE_URL=http://localhost:5251 API=http://localhost:8150 LIGHTHOUSE_MODULE=/path/node_modules/lighthouse/core/index.js \
 *     node e2e/lighthouse.verify.mjs
 *
 *   MIN_PERF=90 MIN_A11Y=90 MIN_BP=90 (defaults) · ROUTES=/login,/dashboard · OUT=e2e/screenshots/lighthouse
 * Site: SITE=1 BASE_URL=http://localhost:5282 ROUTES=/,/docs,/docs/indicadores,/seguranca,/privacidade MIN_PERF=95 …
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const { default: lighthouse } = await import(process.env.LIGHTHOUSE_MODULE ?? "lighthouse");
const BASE = process.env.BASE_URL ?? "http://localhost:5251";
const API = process.env.API ?? "http://localhost:8150";
const SITE = process.env.SITE === "1";
const ROUTES = (process.env.ROUTES ?? (SITE ? "/,/docs,/docs/indicadores,/seguranca,/privacidade" : "/login,/dashboard,/op,/m/work-orders")).split(",");
const MIN = {
  performance: Number(process.env.MIN_PERF ?? (SITE ? 95 : 90)),
  accessibility: Number(process.env.MIN_A11Y ?? (SITE ? 95 : 90)),
  "best-practices": Number(process.env.MIN_BP ?? (SITE ? 95 : 90)),
  ...(SITE ? { seo: Number(process.env.MIN_SEO ?? 95) } : {}),
};
const OUT = process.env.OUT ?? path.join(path.dirname(new URL(import.meta.url).pathname), "screenshots/lighthouse");
fs.mkdirSync(OUT, { recursive: true });
const PORT = Number(process.env.CDP_PORT ?? 9333);
// Lighthouse varies run to run (simulated throttling): the score of a route is the median of RUNS runs (Lighthouse CI does the same)
const RUNS = Number(process.env.RUNS ?? 3);

let tokens = null;
if (!SITE) {
  const r = await fetch(`${API}/api/auth/login`, { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ email: process.env.E2E_EMAIL ?? "admin@factory-monitor.com", password: process.env.E2E_PASSWORD ?? "Admin@1234" }) });
  tokens = await r.json();
  if (!tokens.access_token) throw new Error(`login failed: ${JSON.stringify(tokens)}`);
}

const profile = fs.mkdtempSync(path.join(os.tmpdir(), "fm-lh-"));
const ctx = await chromium.launchPersistentContext(profile, {
  executablePath: "/opt/pw-browsers/chromium", headless: true, args: [`--remote-debugging-port=${PORT}`],
});
if (tokens) {
  const p = await ctx.newPage();
  await p.goto(`${BASE}/login`);
  await p.evaluate((t) => { localStorage.setItem("access_token", t.access_token); localStorage.setItem("refresh_token", t.refresh_token); }, tokens);
  await p.close();
}

let failures = 0;
const rows = [];
for (const route of ROUTES) {
  const runs = [];
  for (let i = 0; i < RUNS; i += 1) {
    runs.push(await lighthouse(`${BASE}${route}`, {
      port: PORT, output: "html", logLevel: "error", disableStorageReset: true,
      onlyCategories: Object.keys(MIN),
    }));
  }
  // median run by performance score
  runs.sort((a, b) => (a.lhr.categories.performance?.score ?? 0) - (b.lhr.categories.performance?.score ?? 0));
  const res = runs[Math.floor(runs.length / 2)];
  const cats = res.lhr.categories;
  const score = Object.fromEntries(Object.keys(MIN).map((k) => [k, Math.round((cats[k]?.score ?? 0) * 100)]));
  const audits = res.lhr.audits;
  rows.push({ route, ...score, lcp: audits["largest-contentful-paint"]?.displayValue, tbt: audits["total-blocking-time"]?.displayValue, cls: audits["cumulative-layout-shift"]?.displayValue });
  const base = path.join(OUT, `${SITE ? "site" : "web"}${route.replace(/\//g, "_") || "_root"}`);
  fs.writeFileSync(`${base}.html`, res.report);
  fs.writeFileSync(`${base}.json`, JSON.stringify(res.lhr));
  for (const [k, min] of Object.entries(MIN)) {
    const ok = score[k] >= min;
    if (!ok) {
      failures += 1;
      const failed = Object.values(audits).filter((a) => a.score !== null && a.score < 0.9 && (res.lhr.categories[k].auditRefs ?? []).some((r) => r.id === a.id && r.weight > 0));
      console.log(`  ✗ ${route} ${k} ${score[k]} < ${min}: ${failed.map((a) => `${a.id}(${a.displayValue ?? a.score})`).join(", ")}`);
    }
  }
}
await ctx.close();
fs.rmSync(profile, { recursive: true, force: true });
console.table(rows);
fs.writeFileSync(path.join(OUT, `${SITE ? "site" : "web"}-summary.json`), JSON.stringify(rows, null, 2));
console.log(failures ? `FAIL — ${failures} score(s) below target` : "PASS — every score at or above target");
process.exit(failures ? 1 : 0);
