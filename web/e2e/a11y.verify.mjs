/**
 * Sprint 7 — accessibility of every main route (axe-core, WCAG 2.1 A/AA) in light + dark, desktop + phone, against a
 * REAL backend (login through the API, real data), plus keyboard checks of the dialogs / drawers / palette and
 * reduced motion. Fails on any serious/critical axe violation or console error.
 *
 *   BASE_URL=http://localhost:5250 API=http://localhost:8150 node e2e/a11y.verify.mjs
 *   ONLY=/dashboard,/oee   SCHEMES=light   VIEWPORTS=phone   (optional filters)
 *
 * Writes e2e/screenshots/a11y/report.json (every violation, any impact); SHOTS=1 also saves a screenshot per
 * route/combination.
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const AXE = fs.readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const BASE = process.env.BASE_URL ?? "http://localhost:5250";
const API = process.env.API ?? "http://localhost:8150";
const ONLY_RAW = process.env.ONLY ?? "";
const EMAIL = process.env.E2E_EMAIL ?? "admin@factory-monitor.com";
const PASSWORD = process.env.E2E_PASSWORD ?? "Admin@1234";
const OUT = process.env.OUT ?? path.join(path.dirname(new URL(import.meta.url).pathname), "screenshots/a11y");
fs.mkdirSync(OUT, { recursive: true });

let failures = 0;
const check = (c, m, got) => { console.log(`  ${c ? "✓" : "✗"} ${m}${c || got === undefined ? "" : `   got=${JSON.stringify(got).slice(0, 600)}`}`); if (!c) failures += 1; };

// ── session (one login for the whole run: the server rate-limits logins) ─────────────────────
async function apiJson(url, opts = {}) {
  const r = await fetch(`${API}${url}`, { ...opts, headers: { "content-type": "application/json", ...(opts.headers ?? {}) } });
  if (!r.ok) throw new Error(`${url} → ${r.status} ${await r.text()}`);
  return r.json();
}
const tokens = await apiJson("/api/auth/login", { method: "POST", body: JSON.stringify({ email: EMAIL, password: PASSWORD }) });
if (!tokens.access_token) throw new Error("login needs MFA or failed — use an account without MFA");
const auth = { headers: { authorization: `Bearer ${tokens.access_token}` } };
const tree = await apiJson("/api/plants/tree", auth);
const plant = tree[0];
const line = plant?.lines.find((l) => l.machines.length) ?? plant?.lines[0];
const machine = line?.machines[0];
const wos = await apiJson("/api/work-orders/", auth).catch(() => []);
const woList = (r) => (Array.isArray(r) ? r : r?.data ?? []);
// /m/work-orders renders the emergency card (badge + "Assumir chamado") only while an emergency call is open:
// make sure one exists so that state is always audited, and cancel it at the end if this run opened it.
let seededEmergency = null;
if (machine && (!ONLY_RAW || ONLY_RAW.split(",").some((r) => r.startsWith("/m/")))) {
  const open = woList(await apiJson("/api/work-orders/?type=corrective&state=open", auth).catch(() => []))
    .filter((w) => w.urgency === "emergency");
  if (!open.length) {
    const wo = await apiJson("/api/work-orders/emergency-call", {
      method: "POST", ...auth,
      body: JSON.stringify({ machine_id: machine.id, title: `Chamado emergencial (auditoria a11y) — ${machine.name}`, note: "a11y.verify" }),
    });
    seededEmergency = wo.id;
    console.log(`seeded emergency call ${wo.id} on ${machine.name}`);
  }
}
async function cleanupEmergency() {
  if (!seededEmergency) return;
  await apiJson(`/api/work-orders/${seededEmergency}/transition`, {
    method: "POST", ...auth, body: JSON.stringify({ to: "cancelled", cancel_reason: "not_needed", note: "a11y.verify cleanup" }),
  }).catch((e) => console.log(`could not cancel the seeded emergency call: ${e.message}`));
}

const PUBLIC = ["/", "/login", "/register", "/esqueci-senha", "/redefinir-senha", "/convite", "/op/login", "/rota-que-nao-existe"];
const APP = [
  "/dashboard", "/oee", "/timeline", "/stops", "/reports/shift", "/reports/scheduled",
  "/factory", "/factory/list", "/factory/layout/edit",
  ...(line ? [`/factory/lines/${line.id}`] : []), ...(machine ? [`/factory/machines/${machine.id}`] : []),
  "/lines", "/machines", "/sensors", "/cameras", "/alerts", "/ai",
  "/maintenance/upcoming", "/maintenance/components", "/maintenance/work-orders", "/maintenance/schedule",
  "/maintenance/failures", "/maintenance/kpis", "/maintenance/reliability", "/maintenance/predictive",
  "/settings/production", "/settings/maintenance-plans", "/settings/gateways", "/settings/users", "/onboarding",
  "/account", "/op", "/m/work-orders", ...(woList(wos)[0] ? [`/m/work-orders/${woList(wos)[0].id}`] : []), ...(seededEmergency ? [`/m/work-orders/${seededEmergency}`] : []), "/m/log",
  ...(line ? [`/tv/lines/${line.id}`] : []),
];
const ONLY = process.env.ONLY ? new Set(process.env.ONLY.split(",")) : null;
const VIEWPORTS = {
  desktop: { viewport: { width: 1366, height: 768 } },
  phone: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 },
};
const SCHEMES = (process.env.SCHEMES ?? "light,dark").split(",");
const VPS = (process.env.VIEWPORTS ?? "desktop,phone").split(",");

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const report = [];

async function newContext(vp, scheme, signedIn, extra = {}) {
  const ctx = await browser.newContext({ ...VIEWPORTS[vp], locale: "pt-BR", timezoneId: "America/Sao_Paulo", colorScheme: scheme, ...extra });
  await ctx.addInitScript(([t, s, inn]) => {
    try {
      localStorage.setItem("fm-theme", s);
      if (inn) { localStorage.setItem("access_token", t.access_token); localStorage.setItem("refresh_token", t.refresh_token); }
      // first-visit tours / hints stay closed during the audit
      localStorage.setItem("fm-onboarding-dismissed", "1");
    } catch { /* ignore */ }
  }, [tokens, scheme, signedIn]);
  return ctx;
}

function track(page) {
  const errors = [];
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const t = m.text();
    // expected network answers the screens handle explicitly (e.g. 404 of an invalid invitation token)
    if (/Failed to load resource: the server responded with a status of (400|404)/.test(t)) return;
    errors.push(t);
  });
  page.on("pageerror", (e) => errors.push(String(e)));
  return errors;
}

async function settle(page) {
  await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => undefined);
  // skeletons / spinners gone (or 8 s, live screens keep polling)
  await page.waitForFunction(() => !document.querySelector('[aria-busy="true"]'), null, { timeout: 8_000 }).catch(() => undefined);
  await page.waitForTimeout(300);
}

async function axe(page, label) {
  await page.addScriptTag({ content: AXE });
  const res = await page.evaluate(async () => {
    // eslint-disable-next-line no-undef
    const r = await axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"] },
      rules: { "region": { enabled: false } },
    });
    return r.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.slice(0, 6).map((n) => ({ target: n.target.join(" "), summary: n.failureSummary?.slice(0, 300) })), count: v.nodes.length }));
  });
  report.push({ label, violations: res });
  return res.filter((v) => v.impact === "serious" || v.impact === "critical");
}

function slug(s) { return s.replace(/[^a-z0-9]+/gi, "_").replace(/^_|_$/g, "") || "root"; }

for (const vp of VPS) {
  for (const scheme of SCHEMES) {
    console.log(`\n▶ ${vp} · ${scheme}`);
    for (const [routes, signedIn] of [[PUBLIC, false], [APP, true]]) {
      const ctx = await newContext(vp, scheme, signedIn);
      const page = await ctx.newPage();
      const errors = track(page);
      for (const r of routes) {
        if (ONLY && !ONLY.has(r)) continue;
        errors.length = 0;
        await page.goto(`${BASE}${r}`, { waitUntil: "domcontentloaded" });
        await settle(page);
        const bad = await axe(page, `${vp}/${scheme}${r}`);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        // 4 combinations × ~45 routes: screenshots only on request (SHOTS=1), the report is always written
        if (process.env.SHOTS === "1") await page.screenshot({ path: path.join(OUT, `${vp}-${scheme}-${slug(r)}.png`) }).catch(() => undefined);
        check(bad.length === 0, `${r} — no serious/critical axe violations`, bad.map((v) => `${v.id} (${v.count}): ${v.nodes.map((n) => n.target).join(" | ")}`));
        check(errors.length === 0, `${r} — no console errors`, errors);
        check(overflow <= 1, `${r} — no horizontal page scroll`, overflow);
      }
      await ctx.close();
    }
  }
}

// ── interactive surfaces: palette, KPI popover, sheet, phone drawer ─────────────────────
if (!ONLY) {
  for (const scheme of SCHEMES) {
    console.log(`\n▶ interactions · ${scheme}`);
    const ctx = await newContext("desktop", scheme, true);
    const page = await ctx.newPage();
    const errors = track(page);
    await page.goto(`${BASE}/dashboard`); await settle(page);

    // command palette: Ctrl+K opens, typing filters, ↓ + Enter navigates, focus returns
    await page.keyboard.press("Control+k");
    const pal = page.getByTestId("palette");
    await pal.waitFor({ timeout: 5000 });
    check(await page.evaluate(() => document.activeElement?.getAttribute("role") === "combobox"), "palette: focus in the search box");
    let bad = await axe(page, `palette/${scheme}`);
    check(bad.length === 0, "palette: no serious/critical axe violations", bad.map((v) => v.id));
    if (machine) {
      await page.keyboard.type(machine.name.slice(0, 6));
      await page.waitForTimeout(400);
      const opts = await page.locator('[role="option"]').count();
      check(opts > 0, `palette: "${machine.name.slice(0, 6)}" finds results`, opts);
      const activeDesc = await page.getAttribute('[role="combobox"]', "aria-activedescendant");
      check(!!activeDesc, "palette: aria-activedescendant points at the active option");
    }
    await page.keyboard.press("Escape");
    check(await pal.count() === 0, "palette: Esc closes");
    await page.keyboard.press("Control+k");
    await page.keyboard.type("ordens");
    await page.waitForTimeout(300);
    await page.keyboard.press("Enter");
    await page.waitForURL(/\/maintenance\/work-orders/, { timeout: 5000 }).catch(() => undefined);
    check(page.url().includes("/maintenance/work-orders"), "palette: Enter navigates (Ordens de serviço)", page.url());

    // KPI popover on the OEE screen
    await page.goto(`${BASE}/oee`); await settle(page);
    const info = page.locator('[data-testid^="kpi-info-"]').first();
    if (await info.count()) {
      await info.focus();
      await page.keyboard.press("Enter");
      check(await info.getAttribute("aria-expanded") === "true", "KPI ⓘ opens with the keyboard");
      const link = page.locator('a[href*="/docs/indicadores#"]').first();
      check(await link.count() > 0, "KPI ⓘ links to the manual section", await link.getAttribute("href").catch(() => null));
      bad = await axe(page, `kpi-popover/${scheme}`);
      check(bad.length === 0, "KPI popover: no serious/critical axe violations", bad.map((v) => v.id));
      await page.keyboard.press("Escape");
      check(await info.getAttribute("aria-expanded") === "false", "KPI ⓘ: Esc closes and focus stays on the button");
    } else check(false, "KPI ⓘ present on /oee");

    // Sheet (notifications): focus trapped, Esc restores focus to the bell
    await page.goto(`${BASE}/dashboard`); await settle(page);
    const bell = page.getByTestId("notification-bell");
    await bell.focus(); await page.keyboard.press("Enter");
    await page.locator('[role="dialog"]').waitFor({ timeout: 5000 });
    for (let i = 0; i < 12; i += 1) await page.keyboard.press("Tab");
    check(await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]')), "sheet: Tab keeps focus inside the dialog");
    bad = await axe(page, `sheet/${scheme}`);
    check(bad.length === 0, "sheet: no serious/critical axe violations", bad.map((v) => v.id));
    await page.keyboard.press("Escape");
    check(await page.evaluate(() => document.activeElement?.getAttribute("data-testid") === "notification-bell"), "sheet: Esc returns focus to the opener");

    // skip link
    await page.goto(`${BASE}/dashboard`); await settle(page);
    await page.keyboard.press("Tab");
    check(await page.evaluate(() => document.activeElement?.textContent?.includes("Pular para o conteúdo")), "skip link is the first Tab stop");
    check(errors.length === 0, "interactions: no console errors", errors);
    await ctx.close();

    // phone drawer: closed drawer is out of the tab order; opens, focus moves in, Esc closes
    const pctx = await newContext("phone", scheme, true);
    const pp = await pctx.newPage();
    await pp.goto(`${BASE}/dashboard`); await settle(pp);
    check(await pp.evaluate(() => getComputedStyle(document.getElementById("app-sidebar")).visibility === "hidden"), "drawer: closed drawer hidden from keyboard / screen readers");
    await pp.getByRole("button", { name: "Abrir menu" }).click();
    await pp.waitForTimeout(400);
    check(await pp.evaluate(() => !!document.activeElement?.closest("#app-sidebar")), "drawer: focus moves into the menu");
    bad = await axe(pp, `drawer/${scheme}`);
    check(bad.length === 0, "drawer: no serious/critical axe violations", bad.map((v) => v.id));
    await pp.keyboard.press("Escape");
    await pp.waitForTimeout(400);
    check(await pp.evaluate(() => getComputedStyle(document.getElementById("app-sidebar")).visibility === "hidden"), "drawer: Esc closes it");
    check(await pp.evaluate(() => document.activeElement?.getAttribute("aria-label") === "Abrir menu"), "drawer: focus returns to the menu button");
    await pctx.close();
  }

  // reduced motion: animations collapse to ~0
  const rctx = await newContext("desktop", "light", true, { reducedMotion: "reduce" });
  const rp = await rctx.newPage();
  await rp.goto(`${BASE}/dashboard`);
  const dur = await rp.evaluate(() => {
    const el = document.createElement("div");
    el.className = "animate-spin";
    document.body.appendChild(el);
    return getComputedStyle(el).animationDuration;
  });
  check(dur === "0.001s" || dur === "1ms", "prefers-reduced-motion: animations disabled", dur);
  await rctx.close();
}

await browser.close();
await cleanupEmergency();
fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(report, null, 2));
const total = report.reduce((n, r) => n + r.violations.filter((v) => v.impact === "serious" || v.impact === "critical").length, 0);
console.log(`\n${failures === 0 ? "PASS" : "FAIL"} — ${failures} failed check(s); ${total} serious/critical violation group(s); report: ${path.join(OUT, "report.json")}`);
process.exit(failures ? 1 : 0);
