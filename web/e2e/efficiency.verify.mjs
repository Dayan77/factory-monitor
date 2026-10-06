/**
 * Browser verification of the Fase 2 efficiency screens: Raio-X, OEE analysis, state timeline,
 * operator stop justification (tablet), shift report (print/PDF), Andon/TV and production settings.
 *
 *   mocked mode (default): every /api call and the realtime socket are served by page.route /
 *     page.routeWebSocket with clearly fictitious data — screenshots light/dark × desktop/tablet/mobile,
 *     live downtime events over the socket, zero console errors, no horizontal page scroll.
 *   real mode (MODE=real): drives a running stack (login + real API): every screen loads from the real
 *     endpoints, a manual stop is registered and justified through the UI, zero console errors.
 *
 *   BASE_URL=http://localhost:5173 OUT=./shots node e2e/efficiency.verify.mjs
 *   MODE=real BASE_URL=http://localhost:5173 node e2e/efficiency.verify.mjs
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.BASE_URL ?? "http://localhost:5173";
const OUT = process.env.OUT ?? "e2e/screenshots";
const MODE = process.env.MODE ?? "mocked";
fs.mkdirSync(OUT, { recursive: true });

const VIEWPORTS = {
  desktop: { width: 1440, height: 900 },
  tablet: { width: 1024, height: 768 },
  mobile: { width: 390, height: 844 },
};
let failures = 0;
function check(cond, msg) {
  if (cond) console.log(`  ✓ ${msg}`);
  else { failures += 1; console.log(`  ✗ ${msg}`); }
}

// ── fixtures (mocked mode) ──────────────────────────────────────────
// Mocked mode runs at a fixed instant — 15:00 in Manaus (19:00Z) of the current UTC date — and the browser
// clock is pinned to it (newPage): the fixtures below go back up to 330 min, which must stay inside the
// plant's "today" whatever the time the suite runs (relative to the real clock it failed from 00:00 to ~04:10).
const NOW = MODE === "real" ? Date.now() : (() => {
  const d = new Date();
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), 19, 0, 0);
})();
const iso = (ms) => new Date(ms).toISOString();
const ago = (min) => iso(NOW - min * 60_000);
const HOUR = 3600e3;
const TREE = [{
  id: "plant-1", name: "Planta Demo", timezone: "America/Manaus",
  lines: [
    { id: "line-1", name: "Linha de Montagem", status_summary: { running: 2, stopped: 0, error: 1, unknown: 0, maintenance: 0 },
      machines: [
        { id: "m-1", name: "Estação de Fixação", status: "running", last_seen_at: ago(0.1), positions_count: 4 },
        { id: "m-2", name: "Robô de Solda", status: "error", last_seen_at: ago(0.2), positions_count: 6 },
        { id: "m-3", name: "Transportador de Saída", status: "running", last_seen_at: ago(0.1), positions_count: 1 },
      ] },
    { id: "line-2", name: "Linha de Usinagem", status_summary: { running: 1, stopped: 1, error: 0, unknown: 0, maintenance: 0 },
      machines: [
        { id: "m-4", name: "Centro de Usinagem", status: "running", last_seen_at: ago(0.1), positions_count: 3 },
        { id: "m-5", name: "Torno CNC", status: "stopped", last_seen_at: ago(1), positions_count: 3 },
      ] },
  ],
}];
const PARETO = [
  { reason_code: "EQP-04", category: "EQP", label: "Falha de automação/software", planned: false, duration_s: 2640, events: 2, pct: 0.31, cumulative_pct: 0.31 },
  { reason_code: "speed_loss", category: "speed_loss", label: "Perda de velocidade", planned: false, duration_s: 1980, events: 0, pct: 0.23, cumulative_pct: 0.54 },
  { reason_code: "SET-01", category: "SET", label: "Setup / troca de produto", planned: false, duration_s: 1500, events: 2, pct: 0.18, cumulative_pct: 0.72 },
  { reason_code: "micro_stops", category: "micro_stops", label: "Micro-paradas", planned: false, duration_s: 1140, events: 23, pct: 0.13, cumulative_pct: 0.85 },
  { reason_code: "MAT-01", category: "MAT", label: "Falta de material", planned: false, duration_s: 780, events: 1, pct: 0.09, cumulative_pct: 0.94 },
  { reason_code: "quality_loss", category: "quality_loss", label: "Qualidade (refugo)", planned: false, duration_s: 510, events: 0, pct: 0.06, cumulative_pct: 1 },
];
const OVERVIEW = {
  plant_id: "plant-1", plant_name: "Planta Demo", productive_day: new Date(NOW - 4 * HOUR).toISOString().slice(0, 10), generated_at: ago(0.5),
  lines: [
    { line_id: "line-1", line_name: "Linha de Montagem", output_machine_id: "m-3", oee_today: 0.684, oee_current_shift: 0.712, current_shift_name: "Turno A",
      availability: 0.842, performance: 0.871, quality: 0.933, good: 1843, scrap: 37, cadence_per_hour: 96.4, data_coverage: 0.99,
      open_downtime_count: 1, unjustified_count: 3, worst_machine: { machine_id: "m-2", machine_name: "Robô de Solda", oee: 0.581 } },
    { line_id: "line-2", line_name: "Linha de Usinagem", output_machine_id: "m-4", oee_today: 0.872, oee_current_shift: 0.88, current_shift_name: "Turno A",
      availability: 0.95, performance: 0.93, quality: 0.987, good: 412, scrap: 5, cadence_per_hour: 31.2, data_coverage: 0.86,
      open_downtime_count: 0, unjustified_count: 0, worst_machine: { machine_id: "m-5", machine_name: "Torno CNC", oee: 0.79 } },
  ],
  top_losses: PARETO.slice(0, 5),
  machines_in_error: [{ machine_id: "m-2", machine_name: "Robô de Solda", line_id: "line-1", last_seen_at: ago(7) }],
  components_near_end: [
    { component_id: "c-3", position_id: "p-3", position_name: "Motor do fuso", machine_id: "m-1", machine_name: "Estação de Fixação", life_consumed_pct: 0.937, life_measure: "hours" },
    { component_id: "c-2", position_id: "p-2", position_name: "Cilindro A2", machine_id: "m-1", machine_name: "Estação de Fixação", life_consumed_pct: 0.82, life_measure: "cycles" },
  ],
};
function oeePayload(url) {
  const start = Date.parse(url.searchParams.get("start") ?? iso(NOW - 8 * HOUR));
  const stop = Date.parse(url.searchParams.get("stop") ?? iso(NOW));
  const group = url.searchParams.get("group_by") ?? "hour";
  const step = group === "day" ? 24 * HOUR : group === "shift" ? 8 * HOUR : HOUR;
  const points = [];
  for (let t = Math.floor(start / step) * step, i = 0; t < stop; t += step, i++) {
    const a = 0.78 + 0.15 * Math.sin(i / 1.7), p = 0.84 + 0.08 * Math.cos(i / 2.3), q = 0.95 + 0.03 * Math.sin(i);
    points.push({ t: iso(t), end: iso(t + step), label: group === "shift" ? `Turno ${"ABC"[i % 3]}` : iso(t), availability: a, performance: p, quality: q, oee: a * p * q });
  }
  return {
    scope: url.searchParams.get("scope"), id: url.searchParams.get("id"), period_start: iso(start), period_end: iso(stop), granularity: group,
    availability: 0.842, performance: 0.871, quality: 0.933, oee: 0.684, teep: 0.41, loading: 0.6, reason: null, ideal_cycle_time_suspect: false,
    pot_s: 28800, pdot_s: 3000, pbt_s: 25200, adot_s: 3980, apt_s: 21220, nodata_s: 600, micro_stop_count: 23, micro_stop_loss_s: 1140,
    micro_stops_per_apt_hour: 3.9, speed_loss_s: 1600, quality_loss_s: 510, ideal_produced_s: 18480, net_productive_s: 17970, produced: 1880,
    good: 1843, scrap: 37, cadence_planned_per_hour: 96.4, cadence_running_per_hour: 106.2, data_coverage: 0.979,
    product_unknown: url.searchParams.get("scope") === "machine", calendar_default: false, points,
  };
}
let DOWNTIME = [
  { id: "d-1", machine_id: "m-2", machine_name: "Robô de Solda", line_id: "line-1", start: ago(7), end: null, duration_s: null, source: "auto_fault", reason_code: null, suggested_reason_code: "EQP-04", planned: false, fault_code: 312, comment: null, unjustified: true },
  { id: "d-2", machine_id: "m-1", machine_name: "Estação de Fixação", line_id: "line-1", start: ago(95), end: ago(80), duration_s: 900, source: "auto_run_state", reason_code: null, suggested_reason_code: "SET-01", planned: false, fault_code: null, comment: null, unjustified: true },
  { id: "d-3", machine_id: "m-3", machine_name: "Transportador de Saída", line_id: "line-1", start: ago(190), end: ago(177), duration_s: 780, source: "auto_cycle_gap", reason_code: null, suggested_reason_code: "MAT-01", planned: false, fault_code: null, comment: null, unjustified: true },
  { id: "d-4", machine_id: "m-1", machine_name: "Estação de Fixação", line_id: "line-1", start: ago(250), end: ago(220), duration_s: 1800, source: "auto_run_state", reason_code: "PLN-01", suggested_reason_code: null, planned: true, fault_code: null, comment: null, unjustified: false },
  { id: "d-5", machine_id: "m-2", machine_name: "Robô de Solda", line_id: "line-1", start: ago(330), end: ago(301), duration_s: 1740, source: "auto_fault", reason_code: "EQP-04", suggested_reason_code: "EQP-04", planned: false, fault_code: 305, comment: "reset do controlador", unjustified: false },
  { id: "d-6", machine_id: "m-5", machine_name: "Torno CNC", line_id: "line-2", start: ago(60), end: ago(40), duration_s: 1200, source: "manual", reason_code: "SET-01", suggested_reason_code: null, planned: false, fault_code: null, comment: "EIXO-20 → EIXO-32", unjustified: false },
];
const REASONS = [
  ["PLN-01", "PLN", "Refeição / pausa", true], ["PLN-02", "PLN", "Reunião / treinamento / 5S programado", true],
  ["EQP-01", "EQP", "Falha mecânica", false], ["EQP-02", "EQP", "Falha elétrica", false], ["EQP-03", "EQP", "Falha pneumática/hidráulica", false],
  ["EQP-04", "EQP", "Falha de automação/software", false], ["EQP-05", "EQP", "Ferramenta/molde/dispositivo", false],
  ["SET-01", "SET", "Setup / troca de produto", false], ["SET-02", "SET", "Ajuste / regulagem após setup", false],
  ["MAT-01", "MAT", "Falta de material / bloqueio por montante", false], ["MAT-02", "MAT", "Saída bloqueada por jusante", false],
  ["OPR-01", "OPR", "Falta de operador", false], ["QLT-01", "QLT", "Aguardando inspeção / problema de qualidade", false],
  ["UTL-01", "UTL", "Utilidades (energia, ar comprimido, água)", false], ["UNK-01", "UNK", "Não justificada", false],
].map(([code, category, label, planned]) => ({ id: `rc-${code}`, tenant_id: null, code, category, label, planned, oee_loss: planned ? "none" : "availability", is_active: true, is_default: true }))
  .concat([{ id: "rc-x", tenant_id: "t-demo", code: "EQP-04-PLC", category: "EQP", label: "Queda de comunicação com o CLP", planned: false, oee_loss: "availability", is_active: true, is_default: false }]);
const SHIFTS = [
  { id: "s-a", tenant_id: "t", plant_id: "plant-1", name: "Turno A", start_local: "06:00", end_local: "14:00", weekdays: [0, 1, 2, 3, 4, 5, 6], breaks: [{ start_local: "10:00", end_local: "11:00", reason_code: "PLN-01" }], is_active: true, created_at: ago(1e4), updated_at: ago(1e4) },
  { id: "s-b", tenant_id: "t", plant_id: "plant-1", name: "Turno B", start_local: "14:00", end_local: "22:00", weekdays: [0, 1, 2, 3, 4, 5, 6], breaks: [{ start_local: "18:00", end_local: "19:00", reason_code: "PLN-01" }], is_active: true, created_at: ago(1e4), updated_at: ago(1e4) },
  { id: "s-c", tenant_id: "t", plant_id: "plant-1", name: "Turno C", start_local: "22:00", end_local: "06:00", weekdays: [0, 1, 2, 3, 4, 5, 6], breaks: [], is_active: true, created_at: ago(1e4), updated_at: ago(1e4) },
];
const PRODUCTS = [
  { id: "p-1", tenant_id: "t", code: "SUP-100", name: "Suporte 100", created_at: ago(1e4) },
  { id: "p-2", tenant_id: "t", code: "SUP-200", name: "Suporte 200", created_at: ago(1e4) },
];
const MACHINE_PRODUCTS = [
  { id: "mp-1", machine_id: "m-1", product_id: "p-1", product_code: "SUP-100", product_name: "Suporte 100", ideal_cycle_time_s: 40, parts_per_cycle: 1, is_default: true, ideal_time_per_part_s: 40 },
  { id: "mp-2", machine_id: "m-1", product_id: "p-2", product_code: "SUP-200", product_name: "Suporte 200", ideal_cycle_time_s: 44, parts_per_cycle: 1, is_default: false, ideal_time_per_part_s: 44 },
];

async function mockApi(page, log) {
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname;
    const json = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (req.method() !== "GET") log.push({ method: req.method(), path: p, body: req.postDataJSON?.() ?? null });
    if (p === "/api/auth/me") return json({ id: "u1", name: "Gestor Demo", email: "gestor@example.com", role: "admin", tenant_id: "t-demo" });
    if (p === "/api/plants/tree") return json(TREE);
    if (p === "/api/overview") return json(OVERVIEW);
    if (p === "/api/oee") return json(oeePayload(url));
    if (p === "/api/losses/pareto") return json(PARETO);
    if (p === "/api/reliability") return json({ failures: 4, incipient: 0, short_faults: 7, operating_h: 412.5, mtbf_h: 103.1, mttr_h: 0.62, mttr_s: 2232, availability_inherent: 0.994, reason: null });
    if (p === "/api/reason-codes/") return json(REASONS);
    if (p === "/api/shifts/") return json(SHIFTS);
    if (p === "/api/calendar-exceptions/") return json([{ id: "ce-1", tenant_id: "t", plant_id: "plant-1", date: "2026-11-02", kind: "holiday", shift_id: null, start_local: null, end_local: null, reason_code: "PLN-04", note: "Finados", created_at: ago(100) }]);
    if (p === "/api/products/") return json(PRODUCTS);
    if (/^\/api\/machines\/[^/]+\/products$/.test(p)) return json(MACHINE_PRODUCTS);
    if (p === "/api/production-runs/") {
      if (req.method() === "POST") return json({ id: "r-2", machine_id: "m-1", product_id: "p-2", product_code: "SUP-200", started_at: iso(NOW), ended_at: null }, 201);
      return json([{ id: "r-1", machine_id: "m-1", product_id: "p-1", product_code: "SUP-100", started_at: ago(200), ended_at: null }]);
    }
    if (p === "/api/downtime/" && req.method() === "POST") return json({ ...DOWNTIME[1], id: "d-new", source: "manual", reason_code: "MAT-01" }, 201);
    if (p === "/api/downtime/") {
      let list = DOWNTIME;
      if (url.searchParams.get("unjustified") === "true") list = list.filter((d) => !d.reason_code);
      if (url.searchParams.get("open") === "true") list = list.filter((d) => !d.end);
      const line = url.searchParams.get("line_id");
      if (line) list = list.filter((d) => d.line_id === line);
      return json(list);
    }
    if (/^\/api\/downtime\/[^/]+$/.test(req.url().split("?")[0].replace(/^https?:\/\/[^/]+/, "")) && req.method() === "PATCH") {
      const id = p.split("/").pop();
      const body = req.postDataJSON();
      DOWNTIME = DOWNTIME.map((d) => (d.id === id ? { ...d, ...body, unjustified: false } : d));
      return json(DOWNTIME.find((d) => d.id === id));
    }
    if (p === "/api/reports/shift") {
      const o = oeePayload(new URL(`http://x/api/oee?scope=line&id=line-1&start=${ago(480)}&stop=${iso(NOW)}&group_by=hour`));
      return json({
        line_id: "line-1", productive_day: url.searchParams.get("productive_day"), shift_id: url.searchParams.get("shift_id"), shift_name: url.searchParams.get("shift_id") ? "Turno A" : null,
        period_start: ago(480), period_end: iso(NOW), oee: o, pareto: PARETO, downtime: DOWNTIME.filter((d) => d.line_id === "line-1"),
        production: [
          { machine_id: "m-1", machine_name: "Estação de Fixação", good: 1870, scrap: 22, total: 1892, availability: 0.88, performance: 0.9, quality: 0.988, oee: 0.78 },
          { machine_id: "m-2", machine_name: "Robô de Solda", good: 1851, scrap: 19, total: 1870, availability: 0.71, performance: 0.86, quality: 0.99, oee: 0.6 },
          { machine_id: "m-3", machine_name: "Transportador de Saída", good: 1843, scrap: 37, total: 1880, availability: 0.84, performance: 0.87, quality: 0.98, oee: 0.72 },
        ],
      });
    }
    return json([]);
  });
}

async function newPage(browser, { theme, viewport }) {
  const context = await browser.newContext({ viewport, colorScheme: theme, deviceScaleFactor: 1, hasTouch: viewport.width < 1100 });
  if (MODE !== "real") await context.clock.install({ time: NOW });   // starts at the fixtures' "now", then runs
  const page = await context.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  return { context, page, errors };
}

async function noHorizontalScroll(page) {
  const r = await page.evaluate(() => {
    const m = document.querySelector("main");
    const docOk = document.documentElement.scrollWidth <= window.innerWidth + 1;
    const ok = docOk && (!m || m.scrollWidth <= m.clientWidth + 1);
    const W = m ? m.getBoundingClientRect().right : window.innerWidth;
    const offenders = ok ? [] : [...document.querySelectorAll("body *")]
      .filter((el) => el.getBoundingClientRect().right > W + 1 && !el.closest(".overflow-x-auto"))
      .slice(0, 5).map((el) => `${el.tagName}.${String(el.className?.baseVal ?? el.className).slice(0, 60)}`);
    return { ok, offenders };
  });
  if (!r.ok) console.log("    overflow:", r.offenders.join(" | "));
  return r.ok;
}

async function shot(page, name) {
  await page.waitForTimeout(400);
  const vp = page.viewportSize();
  const extra = await page.evaluate(() => {
    const m = document.querySelector("main");
    return m && !document.querySelector("[role=dialog]") ? m.scrollHeight - m.clientHeight : 0;
  });
  if (extra > 0) await page.setViewportSize({ width: vp.width, height: vp.height + extra });
  await page.waitForTimeout(150);
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true });
  if (extra > 0) await page.setViewportSize(vp);
}

async function runMocked(browser) {
  for (const theme of ["light", "dark"]) {
    for (const [vpName, viewport] of Object.entries(VIEWPORTS)) {
      console.log(`\n[mocked] ${theme} / ${vpName}`);
      const log = [];
      const { context, page, errors } = await newPage(browser, { theme, viewport });
      await mockApi(page, log);
      let socket = null;
      await page.routeWebSocket(/\/ws\/sensors\//, (ws) => { socket = ws; });
      await page.addInitScript((t) => {
        localStorage.setItem("access_token", "fake.jwt.token");
        localStorage.setItem("fm-theme", t);
      }, theme);
      const tag = `${theme}-${vpName}`;

      // 1) Raio-X
      await page.goto(`${BASE}/dashboard`);
      await page.getByTestId("line-card").first().waitFor();
      check((await page.getByTestId("line-card").count()) === 2, "Raio-X: one card per line");
      check((await page.getByTestId("line-card").first().textContent()).includes("Linha de Montagem"), "line with an open stop first");
      check(await page.getByRole("img", { name: "OEE hoje: 68,4\u00a0%" }).isVisible(), "OEE gauge with accessible value");
      check(await page.getByRole("link", { name: /Robô de Solda/ }).first().isVisible(), "machine in fault listed (live tree)");
      check(await noHorizontalScroll(page), "no horizontal scroll");
      await shot(page, `raiox-${tag}`);

      // drill-down: line → OEE analysis
      await page.getByRole("button", { name: "Ver análise de OEE de Linha de Montagem" }).click();
      await page.waitForURL(/\/oee\?scope=line&id=line-1/);
      check(true, "drill-down line card → /oee?scope=line");

      // 2) OEE analysis
      await page.locator(".recharts-line-curve").first().waitFor();
      // the charts mount one after the other: wait (bounded) for all the lines instead of counting at the first one
      await page.waitForFunction(() => document.querySelectorAll(".recharts-line-curve").length >= 5, null, { timeout: 5000 })
        .catch(() => {});
      check((await page.locator(".recharts-line-curve").count()) >= 5, "trend: A/P/Q/OEE lines + Pareto cumulative");
      check(await page.getByRole("img", { name: /Cascata de tempo: POT 8 h 00 min/ }).isVisible(), "time waterfall POT→…");
      check(await page.getByRole("img", { name: /Pareto de perdas: 6 motivos/ }).isVisible(), "Pareto rendered");
      check(await page.getByText("103,1 h").isVisible(), "MTBF card");
      const lineColor = await page.locator(".recharts-line-curve").first().evaluate((el) => getComputedStyle(el).stroke);
      check(lineColor && lineColor !== "none" && lineColor !== "rgb(0, 0, 0)", `series colour from tokens (${lineColor})`);
      check(await noHorizontalScroll(page), "no horizontal scroll");
      await shot(page, `oee-line-${tag}`);
      await page.getByRole("tab", { name: "Máquina" }).click();
      await page.getByText("Produto não informado").first().waitFor();
      check(true, "'produto não informado' warning on machine scope");
      await page.getByRole("tab", { name: "7 dias" }).click();
      await page.waitForURL(/period=7d/);
      check(true, "period preset in the URL");

      // 3) timeline
      await page.goto(`${BASE}/timeline?line=line-1&period=today`);
      await page.getByTestId("timeline-strip").first().waitFor();
      check((await page.getByTestId("timeline-strip").count()) === 3, "timeline: one strip per machine");
      check((await page.locator('[data-state="error"]').count()) >= 1 && (await page.locator('[data-state="planned"]').count()) >= 1, "fault and planned segments");
      const seg = page.locator('button[data-state="error"]').first();
      await seg.hover();
      check(await page.getByRole("tooltip").isVisible(), "tooltip with the reason on hover");
      await seg.click();
      await page.getByRole("dialog", { name: "Parada" }).waitFor();
      await shot(page, `timeline-${tag}`);
      await page.keyboard.press("Escape");

      // 4) operator: justify stops (live)
      await page.goto(`${BASE}/stops`);
      await page.getByTestId("stop-card").first().waitFor();
      check((await page.getByTestId("stop-card").count()) === 3, "three unjustified stops");
      const box = await page.getByTestId("stop-card").first().boundingBox();
      check(box && box.height >= 72, `big touch target (${Math.round(box?.height ?? 0)} px)`);
      await shot(page, `stops-${tag}`);
      await page.getByTestId("stop-card").nth(1).click();
      const dialog = page.getByRole("dialog", { name: "Justificar parada" });
      await dialog.waitFor();
      check((await dialog.getByRole("radio", { checked: true }).textContent()).includes("Setup / troca de produto"), "suggested reason preselected");
      const radioBox = await dialog.getByRole("radio").first().boundingBox();
      check(radioBox && radioBox.height >= 56, `reason buttons ≥56 px (${Math.round(radioBox?.height ?? 0)} px)`);
      await shot(page, `justify-${tag}`);
      await dialog.getByLabel("Comentário (opcional)").fill("troca para SUP-200");
      await dialog.getByRole("button", { name: "Confirmar motivo" }).click();
      await page.getByText(/justificada: Setup/).waitFor();
      const patch = log.find((l) => l.method === "PATCH");
      check(patch?.path === "/api/downtime/d-2" && patch.body.reason_code === "SET-01" && patch.body.comment === "troca para SUP-200", "PATCH /api/downtime/{id} {reason_code, comment}");
      check((await page.getByTestId("stop-card").count()) === 2, "justified stop leaves the list");
      if (socket) {
        socket.send(JSON.stringify({ type: "downtime", action: "justify", event: { id: "d-3", machine_id: "m-3", start: DOWNTIME[2].start, reason_code: "MAT-01", planned: false } }));
        await page.waitForFunction(() => document.querySelectorAll('[data-testid="stop-card"]').length === 1, null, { timeout: 3000 }).then(
          () => check(true, "stop justified on another tablet disappears live (WebSocket)"),
          () => check(false, "live downtime update over the socket"),
        );
      } else check(false, "realtime socket opened");
      // product change
      await page.getByRole("button", { name: "Trocar produto" }).click();
      const prod = page.getByRole("dialog", { name: "Trocar produto" });
      await prod.getByLabel("Máquina").selectOption("m-1");
      await prod.getByRole("button", { name: /Suporte 200/ }).click();
      await page.getByText("Produção de Suporte 200 iniciada.").waitFor();
      check(log.some((l) => l.method === "POST" && l.path === "/api/production-runs/" && l.body.product_id === "p-2"), "POST /api/production-runs/ (trocar produto)");
      // manual stop
      await page.getByRole("button", { name: "Registrar parada" }).click();
      const manual = page.getByRole("dialog", { name: "Registrar parada" });
      await manual.getByLabel("Máquina").selectOption("m-4");
      await manual.getByRole("radio", { name: /Falta de operador/ }).click();
      await shot(page, `manual-stop-${tag}`);
      await manual.getByRole("button", { name: "Registrar parada" }).click();
      await page.getByText("Parada registrada.").waitFor();
      check(log.some((l) => l.method === "POST" && l.path === "/api/downtime/" && l.body.reason_code === "OPR-01" && l.body.end === null), "POST /api/downtime/ (manual, open)");

      // 5) shift report + print/PDF
      await page.goto(`${BASE}/reports/shift?line=line-1&shift=s-a`);
      await page.getByRole("table", { name: "Produção por máquina" }).waitFor();
      check(await page.getByRole("heading", { name: "Linha de Montagem · Turno A" }).isVisible(), "shift report header");
      await shot(page, `report-${tag}`);
      if (vpName === "desktop") {
        await page.emulateMedia({ media: "print" });
        check(!(await page.locator("aside#app-sidebar").isVisible()), "print: app chrome hidden");
        await page.screenshot({ path: path.join(OUT, `report-print-${theme}.png`), fullPage: true });
        await page.emulateMedia({ media: "screen" });
        if (theme === "light") {
          await page.pdf({ path: path.join(OUT, "shift-report.pdf"), format: "A4", printBackground: true });
          check(fs.statSync(path.join(OUT, "shift-report.pdf")).size > 10_000, "PDF export via print CSS");
        }
      }

      // 6) Andon / TV
      await page.goto(`${BASE}/tv/lines/line-1`);
      await page.getByTestId("tv-timer").waitFor();
      check((await page.getAttribute("html", "data-theme")) === "dark", "TV mode is dark");
      const t1 = await page.getByTestId("tv-timer").textContent();
      await page.waitForTimeout(1600);
      const t2 = await page.getByTestId("tv-timer").textContent();
      check(t1 !== t2, `stop timer ticks (${t1} → ${t2})`);
      check(await page.getByRole("img", { name: "OEE: 71,2\u00a0%" }).isVisible(), "huge OEE (current shift)");
      check(await noHorizontalScroll(page), "no horizontal scroll");
      await shot(page, `tv-${tag}`);
      await page.getByRole("link", { name: "Sair" }).click();
      await page.waitForURL(/dashboard/);
      await page.getByTestId("line-card").first().waitFor();
      check((await page.getAttribute("html", "data-theme")) === theme, "theme restored after leaving TV");

      // 7) settings
      await page.goto(`${BASE}/settings/production`);
      await page.getByTestId("shift-item").first().waitFor();
      await shot(page, `settings-shifts-${tag}`);
      await page.getByRole("tab", { name: "Produtos e ciclos" }).click();
      await page.getByRole("table", { name: "Produtos da máquina" }).waitFor();
      await shot(page, `settings-products-${tag}`);
      await page.getByRole("tab", { name: "Motivos de parada" }).click();
      await page.getByRole("table", { name: "Motivos de parada" }).waitFor();
      check(await noHorizontalScroll(page), "settings: no horizontal page scroll");
      if (vpName === "desktop") await shot(page, `settings-reasons-${tag}`);

      check(errors.length === 0, `zero console errors${errors.length ? ": " + errors.join(" | ") : ""}`);
      DOWNTIME = DOWNTIME.map((d) => (["d-2", "d-3"].includes(d.id) ? { ...d, reason_code: null, unjustified: true, comment: null } : d));
      await context.close();
    }
  }
}

async function runReal(browser) {
  console.log("\n[real] efficiency screens against the running API");
  for (const [vpName, viewport] of [["desktop", VIEWPORTS.desktop], ["tablet", VIEWPORTS.tablet]]) {
    const { context, page, errors } = await newPage(browser, { theme: vpName === "desktop" ? "dark" : "light", viewport });
    await page.goto(`${BASE}/login`);
    await page.getByLabel(/e-mail/i).fill(process.env.E2E_EMAIL ?? "admin@factory-monitor.com");
    await page.getByLabel(/senha/i).fill(process.env.E2E_PASSWORD ?? "Admin@1234");
    await page.getByRole("button", { name: /entrar/i }).click();
    await page.waitForURL(/dashboard/);
    await page.getByTestId("line-card").first().waitFor({ timeout: 30_000 });
    check((await page.getByTestId("line-card").count()) > 0, `[${vpName}] Raio-X from /api/overview`);
    await shot(page, `real-raiox-${vpName}`);

    if (vpName === "tablet") {
      // manual stop → appears as justified in the timeline; then a second, open, unjustified-free stop flow
      await page.goto(`${BASE}/stops`);
      await page.getByRole("heading", { name: "Justificar paradas" }).waitFor();
      await page.getByText(/sem justificativa|Nenhuma parada sem justificativa/).first().waitFor({ timeout: 20_000 });
      await page.getByRole("button", { name: "Registrar parada" }).click();
      const manual = page.getByRole("dialog", { name: "Registrar parada" });
      const firstMachine = await manual.getByLabel("Máquina").locator("option").nth(1).getAttribute("value");
      await manual.getByLabel("Máquina").selectOption(firstMachine);
      const start = new Date(Date.now() - 25 * 60_000);
      const end = new Date(Date.now() - 10 * 60_000);
      const local = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
      await manual.getByLabel("Início").fill(local(start));
      await manual.getByLabel("Ainda parada (em andamento)").uncheck();
      await manual.getByLabel("Fim").fill(local(end));
      await manual.getByRole("radio", { name: /Falta de operador/ }).click();
      const [resp] = await Promise.all([
        page.waitForResponse((r) => r.url().endsWith("/api/downtime/") && r.request().method() === "POST"),
        manual.getByRole("button", { name: "Registrar parada" }).click(),
      ]);
      check(resp.status() === 201, `manual stop POST /api/downtime/ → ${resp.status()}`);
      await page.getByText("Parada registrada.").waitFor();
      await page.goto(`${BASE}/timeline?period=today`);
      await page.getByTestId("timeline-strip").first().waitFor({ timeout: 20_000 });
      await page.locator('button[data-state="stopped"]').first().waitFor({ timeout: 10_000 }).then(
        () => check(true, "manual stop drawn on the real timeline"),
        () => check(false, "manual stop drawn on the real timeline"),
      );
      await shot(page, "real-timeline-tablet");
    } else {
      await page.getByRole("link", { name: "Análise de OEE" }).click();
      await page.getByRole("heading", { name: /^OEE/ }).waitFor();
      await page.getByRole("heading", { name: "Pareto de perdas" }).waitFor();
      await page.waitForTimeout(1500);
      await shot(page, "real-oee-desktop");
      await page.goto(`${BASE}/reports/shift`);
      await page.getByRole("button", { name: "Imprimir / PDF" }).waitFor();
      await page.waitForTimeout(1500);
      await shot(page, "real-report-desktop");
      await page.goto(`${BASE}/settings/production`);
      await page.getByTestId("shift-item").first().waitFor({ timeout: 15_000 });
      check((await page.getByTestId("shift-item").count()) >= 1, "shifts from /api/shifts/");
      await shot(page, "real-settings-desktop");
      const lineHref = await page.goto(`${BASE}/dashboard`).then(() => page.getByRole("link", { name: "Modo TV" }).first().getAttribute("href"));
      await page.goto(`${BASE}${lineHref}`);
      await page.getByTestId("tv-mode").waitFor();
      await page.waitForTimeout(1500);
      await shot(page, "real-tv-desktop");
    }
    check(errors.length === 0, `[${vpName}] zero console errors${errors.length ? ": " + errors.join(" | ") : ""}`);
    await context.close();
  }
}

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
try {
  if (MODE === "real") await runReal(browser);
  else await runMocked(browser);
} finally {
  await browser.close();
}
console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
