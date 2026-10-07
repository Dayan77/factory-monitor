/**
 * Sprint 3 gate — the efficiency screens against a REAL server holding golden scenario C (no mocks).
 *
 * Prerequisite: a server with a scenario C tenant whose stops are NOT yet justified (e2e/scenario_c_replay.py mqtt,
 * PUBLISH_ONLY=1, then let the detector run), an operator user of that tenant, and vite proxied to that server.
 *
 *   BASE_URL=http://localhost:5201 API=http://localhost:8175 IDS=ids.json ADMIN_EMAIL=… OPERATOR_EMAIL=… \
 *     OUT=./shots node e2e/sprint3_real.verify.mjs
 *
 * Flow:
 *  1. tablet (1024×768, light), operator: /stops lists the 6 unjustified stops of the shift; the suggested reason is
 *     preselected for the faults; the operator justifies 08:30 → Falta de material, 13:00 → Setup, confirms the
 *     three EQP faults, leaves 11:50 (UNK-01). Then the API Pareto equals scenario C `expected` exactly.
 *  2. desktop light + dark, admin: OEE analysis of the machine on the scenario day shows OEE/A/P/Q from the golden
 *     numbers; the Pareto chart changed from before the justification (UNK-01 lumped) to the 8 expected reasons;
 *     timeline has the 6 stops; shift report (+ print emulation) shows the shift; TV mode; settings (Turno A);
 *     Raio-X home. Operator sees settings read-only. Zero console errors everywhere, no horizontal scroll.
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.BASE_URL ?? "http://localhost:5201";
const API = process.env.API ?? "http://localhost:8175";
const OUT = process.env.OUT ?? "e2e/screenshots/sprint3";
const IDS = JSON.parse(fs.readFileSync(process.env.IDS, "utf8"));
const ADMIN = { email: process.env.ADMIN_EMAIL ?? IDS.email, password: process.env.ADMIN_PASSWORD ?? "QaOuro#Teste2026" };
const OPER = { email: process.env.OPERATOR_EMAIL, password: process.env.OPERATOR_PASSWORD ?? "QaOper#2026x" };
const C = JSON.parse(fs.readFileSync(new URL("../docs/scenarios/scenario_c_oee_shift.json", import.meta.url), "utf8"));
const X = C.expected;
const SHIFT_DAYS = Number(process.env.SHIFT_DAYS ?? 3);
const shifted = (s) => new Date(Date.parse(s) - SHIFT_DAYS * 86400e3);
const START = shifted(C.shift.start_utc), END = shifted(C.shift.end_utc);
const DAY = new Date(START.getTime() - 4 * 3600e3).toISOString().slice(0, 10);   // Manaus = UTC−4, no DST
fs.mkdirSync(OUT, { recursive: true });

let failures = 0;
const divergences = [];
function check(cond, msg) {
  if (cond) console.log(`  ✓ ${msg}`);
  else { failures += 1; console.log(`  ✗ ${msg}`); }
}

async function api(token, p, params = {}) {
  const u = new URL(API + p);
  for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
  const r = await fetch(u, { headers: { Authorization: `Bearer ${token}` } });
  if (!r.ok) throw new Error(`${p} → ${r.status}`);
  return r.json();
}
async function token(user) {
  const r = await fetch(`${API}/api/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(user) });
  return (await r.json()).access_token;
}

async function newPage(browser, { theme, viewport }) {
  const context = await browser.newContext({ viewport, colorScheme: theme, deviceScaleFactor: 1, hasTouch: viewport.width < 1100, timezoneId: "America/Manaus" });
  const page = await context.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.addInitScript((t) => { try { localStorage.setItem("fm-theme", t); } catch {} }, theme);
  return { context, page, errors };
}
async function login(page, user) {
  await page.goto(`${BASE}/login`);
  await page.getByLabel(/e-mail/i).fill(user.email);
  await page.getByLabel(/senha/i).fill(user.password);
  await page.getByRole("button", { name: /entrar/i }).click();
  await page.waitForURL(/dashboard/);
}
async function noHorizontalScroll(page) {
  return page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
}
async function shot(page, name) {
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true });
}
const oeeUrl = () => `${BASE}/oee?scope=machine&id=${IDS.machine}&period=custom&from=${DAY}&to=${DAY}`;
async function paretoLabel(page) {
  const img = page.getByRole("img", { name: /^Pareto de perdas/ });
  await img.first().waitFor({ timeout: 20_000 });
  return img.first().getAttribute("aria-label");
}

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const adminTok = await token(ADMIN);
const win = { start: START.toISOString(), stop: END.toISOString() };
let paretoBefore = null;
try {
  // ── 0) desktop light, admin, BEFORE justification ─────────────────────
  {
    console.log("\n[before] desktop light, admin");
    const { context, page, errors } = await newPage(browser, { theme: "light", viewport: { width: 1440, height: 900 } });
    await login(page, ADMIN);
    await page.goto(oeeUrl());
    paretoBefore = await paretoLabel(page);
    console.log(`    pareto before: ${paretoBefore}`);
    await shot(page, "oee-before-justification");
    check(errors.length === 0, `zero console errors${errors.length ? ": " + errors.join(" | ") : ""}`);
    await context.close();
  }

  // ── 1) tablet, operator: justify ─────────────────────────────────────
  {
    console.log("\n[operator] tablet light: justify stops");
    const { context, page, errors } = await newPage(browser, { theme: "light", viewport: { width: 1024, height: 768 } });
    await login(page, OPER);
    await page.goto(`${BASE}/stops`);
    await page.getByTestId("stop-card").first().waitFor({ timeout: 20_000 });
    const cards = page.getByTestId("stop-card");
    check((await cards.count()) === 6, `6 unjustified stops listed (got ${await cards.count()})`);
    const box = await cards.first().boundingBox();
    check(box && box.height >= 72, `touch target ${Math.round(box?.height ?? 0)} px`);
    await shot(page, "stops-tablet-before");
    const plan = [["07:12", null, /Quebra|Falha|equipamento/i], ["08:30", /Falta de material/, null], ["09:45", null, /./],
                  ["12:20", null, /./], ["13:00", /Setup/, null]];
    for (const [hhmm, pick, expectPre] of plan) {
      const card = page.getByTestId("stop-card").filter({ hasText: hhmm });
      if ((await card.count()) !== 1) { check(false, `card ${hhmm} found`); continue; }
      await card.click();
      const dialog = page.getByRole("dialog", { name: "Justificar parada" });
      await dialog.waitFor();
      if (pick) {
        await dialog.getByRole("radio", { name: pick }).first().click();
      } else {
        const pre = dialog.getByRole("radio", { checked: true });
        // read the checked radio the moment the dialog is visible (what the operator sees first)
        const want = { "07:12": "EQP-01", "09:45": "EQP-02", "12:20": "EQP-01" }[hhmm];
        const preText = (await pre.first().textContent().catch(() => ""))?.trim() ?? "";
        check((await pre.count()) === 1 && preText.includes(want),
              `${hhmm}: suggested reason ${want} preselected on first paint (${preText})`);
      }
      if (hhmm === "08:30") await shot(page, "justify-dialog-tablet");
      const [resp] = await Promise.all([
        page.waitForResponse((r) => /\/api\/downtime\/[0-9a-f-]+$/.test(r.url()) && r.request().method() === "PATCH"),
        dialog.getByRole("button", { name: "Confirmar motivo" }).click(),
      ]);
      check(resp.status() === 200, `${hhmm}: PATCH /api/downtime/{id} → ${resp.status()} (${(await resp.json()).reason_code})`);
      await dialog.waitFor({ state: "hidden" });
    }
    await page.waitForTimeout(1000);
    const left = await page.getByTestId("stop-card").allTextContents();
    check(left.length === 1 && left[0].includes("11:50"), `only 11:50 (UNK-01) left: ${left.length}`);
    // operator: settings read-only
    await page.goto(`${BASE}/settings/production`);
    await page.getByTestId("shift-item").first().waitFor({ timeout: 15_000 });
    const addBtn = page.getByRole("button", { name: /Novo turno|Adicionar turno/ });
    check((await addBtn.count()) === 0 || (await addBtn.first().isDisabled()), "operator: settings read-only (no add shift)");
    await shot(page, "settings-operator-tablet");
    check(await noHorizontalScroll(page), "no horizontal scroll");
    check(errors.length === 0, `zero console errors${errors.length ? ": " + errors.join(" | ") : ""}`);
    await context.close();
  }

  // ── API after justification == golden ─────────────────────────────────
  console.log("\n[api] after the UI justification");
  const par = await api(adminTok, "/api/losses/pareto", { scope: "machine", id: IDS.machine, ...win });
  const exp = X.loss_pareto.items;
  check(JSON.stringify(par.map((r) => r.reason_code)) === JSON.stringify(exp.map((i) => i[0])), `pareto order ${par.map((r) => r.reason_code).join(",")}`);
  check(par.every((r, i) => Math.abs(r.duration_s / 60 - exp[i][1]) < 1e-6 && Math.abs(r.pct - exp[i][2]) <= 5e-7), "pareto minutes and pct = scenario C");
  const o = await api(adminTok, "/api/oee", { scope: "machine", id: IDS.machine, ...win });
  check(Math.abs(o.oee - X.oee) < 1e-9 && Math.abs(o.availability - X.availability) < 1e-9, `OEE ${o.oee} A ${o.availability}`);

  // ── 2) desktop light + dark, admin ────────────────────────────────────
  for (const theme of ["light", "dark"]) {
    console.log(`\n[admin] desktop ${theme}`);
    const { context, page, errors } = await newPage(browser, { theme, viewport: { width: 1440, height: 900 } });
    await login(page, ADMIN);
    await page.getByTestId("line-card").first().waitFor({ timeout: 30_000 });
    check((await page.getByTestId("line-card").count()) === 1, "Raio-X: one line card");
    check((await page.getAttribute("html", "data-theme")) === theme, `theme ${theme}`);
    check(await noHorizontalScroll(page), "Raio-X: no horizontal scroll");
    await shot(page, `raiox-${theme}`);

    await page.goto(oeeUrl());
    const after = await paretoLabel(page);
    console.log(`    pareto after: ${after}`);
    check(after !== paretoBefore, "Pareto chart changed after the justification");
    const ring = page.getByRole("img", { name: /^OEE[^:]*: / }).first();
    const ringLabel = await ring.getAttribute("aria-label");
    check(/7[67]([,.]9)?\s?%/.test(ringLabel), `OEE gauge = golden 0,769 (${ringLabel})`);
    if (!/76,9\s?%/.test(ringLabel)) divergences.push(`S3-5: percent shown with 0 decimals (${ringLabel}); kpis.md S3-5 requires 1 decimal (76,9 %)`);
    const body = await page.locator("main").textContent();
    check(/83([,.]1)?\s?%/.test(body) && /94([,.]4)?\s?%/.test(body) && /98([,.]0)?\s?%/.test(body), "A 83 %, P 94 %, Q 98 % visible");
    check(await page.getByRole("img", { name: /Cascata de tempo: POT 8 h 00 min/ }).isVisible(), "waterfall POT 8 h 00 min");
    check(!/Produto não informado/.test(body), "no 'produto não informado' (run open)");
    check(await noHorizontalScroll(page), "OEE: no horizontal scroll");
    await shot(page, `oee-after-${theme}`);

    await page.goto(`${BASE}/timeline?line=${IDS.line}&period=custom&from=${DAY}&to=${DAY}`);
    await page.getByTestId("timeline-strip").first().waitFor({ timeout: 20_000 });
    const segs = await page.locator('button[data-state="stopped"], button[data-state="error"], button[data-state="planned"]').count();
    check(segs >= 6, `timeline: ≥6 stop segments (${segs})`);
    await shot(page, `timeline-${theme}`);

    await page.goto(`${BASE}/reports/shift?line=${IDS.line}&day=${DAY}&shift=${IDS.shift}`);
    await page.getByRole("table", { name: "Produção por máquina" }).waitFor({ timeout: 20_000 });
    const rep = await page.locator("main").textContent();
    check(rep.includes("Turno A") && /441/.test(rep), "shift report: Turno A, 441 good");
    await shot(page, `report-${theme}`);
    await page.emulateMedia({ media: "print" });
    check(!(await page.locator("aside#app-sidebar").isVisible()), "print: app chrome hidden");
    await page.screenshot({ path: path.join(OUT, `report-print-${theme}.png`), fullPage: true });
    await page.emulateMedia({ media: "screen" });

    await page.goto(`${BASE}/settings/production`);
    await page.getByTestId("shift-item").first().waitFor({ timeout: 15_000 });
    check((await page.getByTestId("shift-item").first().textContent()).includes("Turno A"), "settings: Turno A");
    await shot(page, `settings-${theme}`);

    await page.goto(`${BASE}/tv/lines/${IDS.line}`);
    await page.getByTestId("tv-mode").waitFor({ timeout: 15_000 });
    check((await page.getAttribute("html", "data-theme")) === "dark", "TV mode dark");
    await shot(page, `tv-${theme}`);
    await page.getByRole("link", { name: "Sair" }).click();
    await page.waitForURL(/dashboard/);
    await page.getByTestId("line-card").first().waitFor({ timeout: 15_000 });
    await page.waitForTimeout(1000);
    check((await page.getAttribute("html", "data-theme")) === theme, "theme restored after TV");

    check(errors.length === 0, `zero console errors${errors.length ? ": " + errors.join(" | ") : ""}`);
    await context.close();
  }
} finally {
  await browser.close();
}
for (const d of divergences) console.log(`  ! spec divergence: ${d}`);
console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
