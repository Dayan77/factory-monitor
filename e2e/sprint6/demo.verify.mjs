/**
 * Sprint 6 acceptance 7 (docs/sprint6.md §5, §6.7) on the LIVE demo tenant (fed by the demo virtual plant through
 * its real gateway):
 *
 *   a. the public site's "Ver demonstração" lands on {APP_URL}/demo (site/src/lib/demo.ts) → that route must open
 *      the dashboard in ≤ 5 s
 *   b. the app landing page button "Ver demonstração" → dashboard in ≤ 5 s
 *   c. every screen: demo banner visible, no e-mail address in the page text NOR in any JSON the page received,
 *      no console errors, no horizontal overflow; blocked areas say "Indisponível na demonstração"
 *      (desktop 1366×768 light + dark, tablet 1280×800, phone 390×844)
 *   d. every write route of the OpenAPI route table answers 403 to the demo_viewer token (except AI questions)
 *
 *   BASE_URL=http://localhost:5173 API=http://localhost:8000 node e2e/sprint6/demo.verify.mjs
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.BASE_URL ?? "http://localhost:5173";
const API = process.env.API ?? "http://localhost:8000";
const REPO = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../..");
const OUT = process.env.OUT ?? path.join(REPO, "e2e/screenshots/sprint6/demo");
fs.mkdirSync(OUT, { recursive: true });
let failures = 0;
const check = (c, m, got) => { console.log(`  ${c ? "✓" : "✗"} ${m}${c || got === undefined ? "" : `   got=${JSON.stringify(got).slice(0, 500)}`}`); if (!c) failures += 1; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const EMAIL = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/;
const ALLOWED_EMAIL = /^(?:contato|suporte|privacidade)@factoryconnect\.tech$/;   // public contact addresses, not users

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
async function newPage(vp, scheme) {
  const ctx = await browser.newContext({ viewport: vp, locale: "pt-BR", timezoneId: "America/Sao_Paulo", colorScheme: scheme, ...(vp.width < 500 ? { isMobile: true, hasTouch: true } : {}) });
  const page = await ctx.newPage();
  const errors = []; const jsonEmails = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("response", async (r) => {
    if (!r.url().includes("/api/") || !(r.headers()["content-type"] ?? "").includes("json")) return;
    const t = await r.text().catch(() => "");
    const m = t.match(new RegExp(EMAIL, "g"))?.filter((e) => !ALLOWED_EMAIL.test(e));
    if (m?.length) jsonEmails.push(`${r.url().replace(API, "").replace(BASE, "")}: ${[...new Set(m)].join(",")}`);
  });
  return { ctx, page, errors, jsonEmails };
}

// a. the site's target URL
{
  const { ctx, page } = await newPage({ width: 1366, height: 768 }, "light");
  const t0 = Date.now();
  await page.goto(`${BASE}/demo`);
  const reached = await page.waitForURL((u) => u.pathname.startsWith("/dashboard"), { timeout: 5000 }).then(() => true).catch(() => false);
  const body = (await page.locator("body").innerText()).trim();
  await page.screenshot({ path: path.join(OUT, "site_target_demo_route.png") });
  check(reached && await page.getByTestId("demo-banner").isVisible(), `site button target ${BASE}/demo opens the dashboard in ≤ 5 s`, { url: page.url(), ms: Date.now() - t0, body: body.slice(0, 120) || "(blank page)" });
  await ctx.close();
}

// b. app landing button
let demoToken = null;
{
  const { ctx, page, errors } = await newPage({ width: 1366, height: 768 }, "light");
  await page.goto(`${BASE}/`);
  const btn = page.getByTestId("open-demo").first();
  await btn.waitFor({ timeout: 15000 });
  const t0 = Date.now();
  await btn.click();
  await page.waitForURL((u) => u.pathname.startsWith("/dashboard"), { timeout: 10000 }).catch(() => {});
  await page.getByTestId("demo-banner").waitFor({ timeout: 10000 }).catch(() => {});
  const dt = Date.now() - t0;
  check(page.url().includes("/dashboard") && dt <= 5000, `landing "Ver demonstração" → dashboard with banner in ${dt} ms (≤ 5000)`, page.url());
  demoToken = await page.evaluate(() => { for (const k of Object.keys(localStorage)) { const v = localStorage.getItem(k) ?? ""; const m = v.match(/eyJ[\w-]+\.[\w-]+\.[\w-]+/); if (m) return m[0]; } return null; });
  check(errors.length === 0, "no console errors opening the demo", errors.slice(0, 3));
  await ctx.close();
}
if (!demoToken) demoToken = (await (await fetch(`${API}/api/public/demo-session`, { method: "POST" })).json()).access_token;
const H = { authorization: `Bearer ${demoToken}`, "content-type": "application/json" };
const me = await (await fetch(`${API}/api/auth/me`, { headers: H })).json();
check(me.roles?.includes("demo_viewer") && me.email == null, "/auth/me: demo_viewer, email null", { roles: me.roles, email: me.email });
const tree = await (await fetch(`${API}/api/plants/tree`, { headers: H })).json();
const plant = tree[0]; const line = plant.lines[0]; const machine = line.machines[0];

// c. screens
const SCREENS = ["/dashboard", "/factory", "/factory/list", `/factory/lines/${line.id}`, `/factory/machines/${machine.id}`, "/oee", "/timeline", "/stops",
  "/reports/shift", "/maintenance/work-orders", "/maintenance/upcoming", "/maintenance/components", "/maintenance/schedule", "/maintenance/failures",
  "/maintenance/kpis", "/maintenance/reliability", "/maintenance/predictive", "/lines", "/machines", "/sensors", "/alerts", "/ai",
  "/reports/scheduled", "/settings/production", "/settings/maintenance-plans", "/settings/gateways", "/cameras", "/admin", "/onboarding/1", `/tv/plants/${plant.id}`];
const BLOCKED = ["/settings/gateways", "/cameras", "/admin", "/onboarding/1"];
const VPS = [["desktop", { width: 1366, height: 768 }, "light"], ["desktop", { width: 1366, height: 768 }, "dark"], ["tablet", { width: 1280, height: 800 }, "dark"], ["phone", { width: 390, height: 844 }, "light"]];
const summary = [];
for (const [vpName, vp, scheme] of VPS) {
  const { ctx, page, errors, jsonEmails } = await newPage(vp, scheme);
  await page.goto(`${BASE}/`);
  await page.getByTestId("open-demo").first().click();
  await page.waitForURL((u) => u.pathname.startsWith("/dashboard"), { timeout: 15000 });
  for (const s of SCREENS) {
    const nErr = errors.length;
    await page.goto(`${BASE}${s}`);
    await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
    await sleep(600);
    const banner = await page.getByTestId("demo-banner").isVisible().catch(() => false);
    const text = await page.locator("body").innerText();
    const emails = (text.match(new RegExp(EMAIL, "g")) ?? []).filter((e) => !ALLOWED_EMAIL.test(e));
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    const blockedMsg = /Indisponível na demonstração/.test(text);
    const tv = s.startsWith("/tv/");
    const row = { vp: `${vpName}/${scheme}`, s, banner, emails, overflow, blockedMsg, errors: errors.slice(nErr) };
    summary.push(row);
    if (!banner && !tv) check(false, `${row.vp} ${s}: demo banner visible`);
    if (tv && !banner) console.log(`  · ${row.vp} ${s}: TV view without banner (kiosk view)`);
    if (emails.length) check(false, `${row.vp} ${s}: no e-mail in page text`, emails);
    if (overflow) check(false, `${row.vp} ${s}: no horizontal overflow`);
    if (BLOCKED.includes(s) && !blockedMsg) check(false, `${row.vp} ${s}: blocked area says "Indisponível na demonstração"`, text.slice(0, 200));
    if (row.errors.length) check(false, `${row.vp} ${s}: no console errors`, row.errors.slice(0, 3));
    await page.screenshot({ path: path.join(OUT, `${vpName}_${scheme}${s.replace(/[/:]/g, "_").replace(/[0-9a-f-]{36}/g, "id")}.png`) });
  }
  if (jsonEmails.length) check(false, `${vpName}/${scheme}: no e-mail in any JSON response`, [...new Set(jsonEmails)].slice(0, 10));
  await ctx.close();
}
const per = (k) => summary.filter((r) => r[k]).length;
check(summary.filter((r) => !r.s.startsWith("/tv/")).every((r) => r.banner), `banner on every screen (${summary.length} screen×viewport checks)`, summary.filter((r) => !r.banner).map((r) => `${r.vp} ${r.s}`));
check(summary.every((r) => r.emails.length === 0), "no e-mail address on any demo screen");
check(per("overflow") === 0, "no horizontal overflow on any demo screen", summary.filter((r) => r.overflow).map((r) => `${r.vp} ${r.s}`));

// d. route table sweep
const oa = await (await fetch(`${API}/api/openapi.json`)).json();
const writes = Object.entries(oa.paths).flatMap(([p, ops]) => Object.keys(ops).filter((m) => ["post", "put", "patch", "delete"].includes(m)).map((m) => [m.toUpperCase(), p]));
const ALLOW = new Set(["POST /api/ai/chat", "POST /api/ai/chat/stream"]);
const notBlocked = [];
for (const [m, p] of writes) {
  if (ALLOW.has(`${m} ${p}`)) continue;
  const url = p.replace(/\{[^}]+\}/g, () => crypto.randomUUID());
  const r = await fetch(`${API}${url}`, { method: m, headers: H, body: m === "DELETE" ? undefined : "{}" });
  const body = await r.json().catch(() => null);
  if (r.status !== 403 || !/Modo demonstração/.test(JSON.stringify(body))) notBlocked.push(`${m} ${p} → ${r.status}`);
}
check(notBlocked.length === 0, `every write route (${writes.length - ALLOW.size} of the OpenAPI table) → 403 "Modo demonstração" for demo_viewer`, notBlocked);
await browser.close();
console.log(`\n${failures === 0 ? "OK" : "FAIL"} (${failures} failure(s))`);
process.exit(failures === 0 ? 0 : 1);
