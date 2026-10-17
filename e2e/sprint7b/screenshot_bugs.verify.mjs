/**
 * Sprint 7b gate (i) — the three screenshot bugs, verified in the browser against a REAL stack with the live
 * virtual plant (no mocks):
 *   B-1 timeline: periods without data are painted "sem dados" (data-state="nodata"), matching
 *       GET /api/downtime/no-data, never green "sem parada registrada";
 *   B-2 reliability: a failure registered TODAY appears in the default window — API (/api/maintenance/reliability
 *       without start/stop) AND the page (/maintenance/reliability: the request it sends must reach "now");
 *   B-3 stale open failure: an open failure older than FAILURE_STALE_OPEN_H (72 h) does not force the live
 *       "failure" state; the marker carries the stale_failure badge and "falha aberta há muito tempo — verificar"
 *       (API + map page).
 *
 * Data it creates (tenant of the admin, notes end in "(qa-7b)"): one closed failure today, one failure opened
 * 96 h ago that is restored at the end of the run.
 *
 *   BASE_URL=http://localhost:5173 API=http://localhost:8000 node e2e/sprint7b/screenshot_bugs.verify.mjs
 * Exit code 0 = every check passed. Screenshots in e2e/screenshots/sprint7b/.
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.BASE_URL ?? "http://localhost:5173";
const API = (process.env.API ?? "http://localhost:8000").replace(/\/$/, "");
const EMAIL = process.env.E2E_EMAIL ?? "admin@factory-monitor.com";
const PASSWORD = process.env.E2E_PASSWORD ?? "Admin@1234";
const OUT = process.env.OUT ?? path.join(path.dirname(new URL(import.meta.url).pathname), "../screenshots/sprint7b");
fs.mkdirSync(OUT, { recursive: true });

let failures = 0;
const check = (cond, msg, got) => {
  if (cond) console.log(`  ✓ ${msg}`);
  else { failures += 1; console.log(`  ✗ ${msg}${got !== undefined ? ` — got: ${JSON.stringify(got).slice(0, 400)}` : ""}`); }
};

const tok = (await (await fetch(`${API}/api/auth/login`, { method: "POST", headers: { "content-type": "application/json" },
  body: JSON.stringify({ email: EMAIL, password: PASSWORD }) })).json()).access_token;
const api = async (method, p, body) => {
  const r = await fetch(`${API}${p}`, { method, headers: { authorization: `Bearer ${tok}`, "content-type": "application/json" },
    body: body ? JSON.stringify(body) : undefined });
  const txt = await r.text();
  let j; try { j = JSON.parse(txt); } catch { j = txt; }
  if (!r.ok) throw new Error(`${method} ${p} → ${r.status} ${txt.slice(0, 300)}`);
  return j;
};
const iso = (ms) => new Date(ms).toISOString();
const NOW = Date.now();

const plants = await api("GET", "/api/plants/");
const plant = plants[0];
const view = await api("GET", `/api/plants/${plant.id}/layout/published`).catch(() => api("GET", `/api/plants/${plant.id}/layout/auto`));
const machines = view.machines;
console.log(`plant ${plant.name} (${plant.timezone}), ${machines.length} machines, states:`,
  Object.entries(machines.reduce((a, m) => ({ ...a, [m.state]: (a[m.state] ?? 0) + 1 }), {})));

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const consoleErrors = [];
page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
page.on("pageerror", (e) => consoleErrors.push(String(e)));

await page.goto(`${BASE}/login`);
await page.fill("#login-email", EMAIL);
await page.fill("#login-password", PASSWORD);
await page.click("button[type=submit]");
await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });

// ── B-1 timeline "sem dados" ─────────────────────────────────────────────────
console.log("B-1 timeline no-data");
{
  const noDataReq = page.waitForResponse((r) => r.url().includes("/api/downtime/no-data"), { timeout: 30000 }).catch(() => null);
  await page.goto(`${BASE}/timeline`);
  const resp = await noDataReq;
  check(resp && resp.ok(), "page requests GET /api/downtime/no-data (200)", resp && resp.status());
  const body = resp ? await resp.json() : [];
  const withGaps = body.filter((r) => r.intervals.length > 0);
  const gapMs = withGaps.reduce((a, r) => a + r.intervals.reduce((b, i) => b + (Date.parse(i.end) - Date.parse(i.start)), 0), 0);
  console.log(`    API: ${body.length} machines, ${withGaps.length} with gaps, ${(gapMs / 3600e3).toFixed(1)} machine-h without data`);
  await page.waitForSelector("[data-testid=timeline-strip]", { timeout: 30000 });
  await page.waitForTimeout(1500);
  const nodataSegs = await page.locator("[data-testid=timeline-strip] [data-state=nodata]").count();
  check(withGaps.length > 0, "the live window has machines with gaps (precondition: the plant did not report the whole day)", withGaps.length);
  check(nodataSegs > 0, `the strips paint ${nodataSegs} segment(s) as "sem dados" (data-state=nodata)`, nodataSegs);
  check(await page.getByText(/sem dados/i).first().isVisible().catch(() => false), "legend/label 'sem dados' visible");
  check(!(await page.getByText("Não foi possível verificar os períodos sem dados").isVisible().catch(() => false)), "no failure warning while the gaps query works");
  await page.screenshot({ path: path.join(OUT, "b1_timeline_nodata.png"), fullPage: true });

  // gaps query failing → warning instead of silently green
  await page.route("**/api/downtime/no-data**", (r) => r.fulfill({ status: 500, body: "{}" }));
  await page.reload();
  const warn = await page.getByText("Não foi possível verificar os períodos sem dados").first().waitFor({ timeout: 30000 }).then(() => true).catch(() => false);
  check(warn, "gaps query 500 → visible warning (no silent green)");
  await page.screenshot({ path: path.join(OUT, "b1_timeline_nodata_error.png"), fullPage: true });
  await page.unroute("**/api/downtime/no-data**");
}

// ── B-2 reliability window reaches now ───────────────────────────────────────
console.log("B-2 reliability default window");
// a RUNNING machine: S7-23 flags an old open record as stale only when the data contradicts it (machine runs again)
const target = machines.find((m) => m.state === "running") ?? machines.find((m) => m.state !== "failure") ?? machines[0];
const today = await api("POST", "/api/failures/", { machine_id: target.machine_id, failed_at: iso(NOW - 40 * 60e3),
  restored_at: iso(NOW - 25 * 60e3), severity: "critical", note: "QA gate: falha de hoje (qa-7b)" });
{
  const rel = await api("GET", "/api/maintenance/reliability?group_by=machine");
  const rows = Array.isArray(rel) ? rel : rel.rows ?? rel.items ?? [];
  const pe = rel.period_end ?? rows[0]?.period_end;
  console.log(`    API default period_end=${pe}`);
  check(pe && Date.parse(pe) >= NOW - 120e3, "API default window ends at ~now (not today 00:00)", pe);
  const fl = await api("GET", `/api/failures/?machine_id=${target.machine_id}`);
  const list = Array.isArray(fl) ? fl : fl.items ?? [];
  check(list.some((f) => f.id === today.id), "today's failure listed by /api/failures/");

  const relReq = page.waitForRequest((r) => r.url().includes("/api/maintenance/reliability"), { timeout: 30000 }).catch(() => null);
  await page.goto(`${BASE}/maintenance/reliability`);
  const req = await relReq;
  const u = req ? new URL(req.url()) : null;
  const stop = u?.searchParams.get("stop");
  console.log(`    page request: ${u ? u.search : "(none)"}`);
  check(req && (!stop || Date.parse(stop) >= Date.parse(today.failed_at)),
    "page's reliability request covers today's failure (stop omitted or ≥ failed_at)", stop);
  await page.waitForTimeout(2000);
  const label = await page.getByText(/Últimos 12 meses/).first().textContent().catch(() => null);
  console.log(`    page label: ${label}`);
  check(!(label ?? "").includes("ontem"), "page label does not say the window ends 'ontem'", label);
  await page.screenshot({ path: path.join(OUT, "b2_reliability_window.png"), fullPage: true });
}

// ── B-3 stale open failure ───────────────────────────────────────────────────
console.log("B-3 stale open failure");
const stale = await api("POST", "/api/failures/", { machine_id: target.machine_id, failed_at: iso(NOW - 96 * 3600e3),
  severity: "critical", note: "QA gate: falha aberta antiga (qa-7b)" });
try {
  const fl = await api("GET", `/api/failures/?machine_id=${target.machine_id}`);
  const list = Array.isArray(fl) ? fl : fl.items ?? [];
  const f = list.find((x) => x.id === stale.id);
  check(f && f.open_stale === true, "/api/failures/ marks it open_stale=true", f && f.open_stale);
  const v = await api("GET", `/api/plants/${plant.id}/layout/published`).catch(() => api("GET", `/api/plants/${plant.id}/layout/auto`));
  const m = v.machines.find((x) => x.machine_id === target.machine_id);
  console.log(`    marker: state=${m.state} text="${m.text}" badges=${JSON.stringify(m.badges.map((b) => b.kind))}`);
  check(m.state !== "failure", `live state is not forced to 'failure' (was '${target.state}', now '${m.state}')`, m.state);
  check(m.badges.some((b) => b.kind === "stale_failure" && b.failure_id === stale.id), "marker has the stale_failure badge");
  check(/falha aberta há muito tempo — verificar/.test(m.text), "marker text carries the warning phrase", m.text);
  check(m.detail?.stale_failure_id === stale.id, "detail.stale_failure_id set");

  await page.goto(`${BASE}/factory`);
  await page.waitForTimeout(4000);
  const phraseInDom = await page.evaluate(() => document.body.textContent.includes("falha aberta há muito tempo"));
  check(phraseInDom, "map page (/factory) shows 'falha aberta há muito tempo — verificar' for the machine");
  await page.screenshot({ path: path.join(OUT, "b3_map_stale_failure.png"), fullPage: true });
  await page.goto(`${BASE}/factory/machines/${target.machine_id}`);
  await page.waitForTimeout(3000);
  await page.screenshot({ path: path.join(OUT, "b3_machine_stale_failure.png"), fullPage: true });
} finally {
  await api("PATCH", `/api/failures/${stale.id}`, { restored_at: iso(Date.now() - 95 * 3600e3) }).catch((e) => console.log("cleanup:", e.message));
}

const relevantErrors = consoleErrors.filter((e) => !/500 \(Internal Server Error\)|status of 500/.test(e));
check(relevantErrors.length === 0, "zero console errors (except the injected 500)", relevantErrors.slice(0, 5));
await browser.close();
console.log(failures ? `\n${failures} check(s) FAILED` : "\nall checks passed");
process.exit(failures ? 1 : 0);
