/**
 * Maintenance screens against a REAL running API (Sprint 4 backend), seeded with scripts/seed_dev.py
 * (planejador@ / tecnico@ / supervisor@factory-monitor.com). Logs in through the UI, opens every
 * planner screen (no error states, zero console errors), creates a plan through the form, and runs the
 * technician flow on a 360 px phone: start a scheduled WO, tick the checklist, go offline (queue), come
 * back online (sync) and checks the server state through the API.
 *
 *   BASE_URL=http://localhost:5203 API=http://localhost:8150 node e2e/maintenance.real.verify.mjs
 *   (the app must proxy /api to API; needs a WO scheduled today for the technician — see the README in the
 *    report or create one through the planner UI)
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.BASE_URL ?? "http://localhost:5203";
const API = process.env.API ?? "http://localhost:8150";
const OUT = process.env.OUT ?? "e2e/screenshots/maintenance-real";
fs.mkdirSync(OUT, { recursive: true });
let failures = 0;
function check(cond, msg) {
  if (cond) console.log(`  ✓ ${msg}`);
  else { failures += 1; console.log(`  ✗ ${msg}`); }
}

async function api(method, p, token, body) {
  const r = await fetch(`${API}${p}`, { method, headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, data: await r.json().catch(() => null) };
}
const token = async (email, password) => (await api("POST", "/api/auth/login", null, { email, password })).data.access_token;

async function newPage(browser, viewport, mobile = false) {
  const context = await browser.newContext({ viewport, hasTouch: mobile, isMobile: mobile, timezoneId: "America/Manaus", locale: "pt-BR" });
  const page = await context.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  return { context, page, errors };
}

async function login(page, email, password) {
  await page.goto(`${BASE}/login`);
  await page.getByLabel(/e-mail/i).fill(email);
  await page.getByLabel(/senha/i).fill(password);
  await page.getByRole("button", { name: /entrar/i }).click();
}

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
try {
  // ── planner desktop ──
  console.log("\n[real] planner desktop");
  {
    const { context, page, errors } = await newPage(browser, { width: 1440, height: 900 });
    await login(page, "planejador@factory-monitor.com", process.env.PLANNER_PASSWORD ?? "Planner@1234");
    await page.waitForURL(/dashboard/);
    const screens = [
      ["/maintenance/upcoming", () => page.getByTestId("plan-row").first().waitFor({ timeout: 20_000 })],
      ["/maintenance/work-orders", () => page.getByTestId("wo-card").first().waitFor({ timeout: 20_000 })],
      ["/maintenance/work-orders?view=list", () => page.getByTestId("wo-row").first().waitFor({ timeout: 20_000 })],
      ["/maintenance/schedule", () => page.getByRole("table", { name: /Quadro da semana/ }).waitFor({ timeout: 20_000 })],
      ["/maintenance/components", () => page.getByRole("heading", { name: "Componentes por % de vida" }).first().waitFor()],
      ["/maintenance/failures", () => page.getByRole("heading", { name: "Falhas", exact: true }).first().waitFor()],
      ["/maintenance/kpis", () => page.getByTestId("kpi-pmp").waitFor({ timeout: 20_000 })],
      ["/maintenance/reliability", () => page.getByRole("heading", { name: "Confiabilidade", exact: true }).first().waitFor()],
      ["/settings/maintenance-plans", () => page.getByTestId("plan-settings-row").first().waitFor({ timeout: 20_000 })],
    ];
    for (const [url, ready] of screens) {
      await page.goto(`${BASE}${url}`);
      await ready().then(() => check(true, `${url} loads from the real API`), () => check(false, `${url} loads from the real API`));
      await page.waitForTimeout(800);
      check((await page.getByRole("alert").filter({ hasText: /Não foi possível/ }).count()) === 0, `${url}: no error state`);
      await page.screenshot({ path: path.join(OUT, `${url.replace(/[/?=]/g, "_")}.png`) });
    }
    // create a plan through the form
    await page.getByRole("button", { name: "Novo plano" }).first().click();
    const pd = page.getByRole("dialog", { name: "Novo plano de manutenção" });
    await pd.getByLabel("Nome").fill(`Plano e2e ${Date.now() % 100000}`);
    const machine = await pd.getByLabel("Máquina", { exact: true }).locator("option").nth(1).getAttribute("value");
    await pd.getByLabel("Máquina", { exact: true }).selectOption(machine);
    await pd.getByLabel("A cada (dias corridos)").fill("30");
    await pd.getByLabel("Item 1 do checklist").fill("Inspecionar");
    const [resp] = await Promise.all([
      page.waitForResponse((r) => r.url().endsWith("/api/maintenance-plans/") && r.request().method() === "POST"),
      pd.getByRole("button", { name: "Criar plano" }).click(),
    ]);
    check(resp.status() === 201, `POST /api/maintenance-plans/ → ${resp.status()}`);
    // detail with audit trail
    await page.goto(`${BASE}/maintenance/work-orders`);
    await page.getByTestId("wo-card").first().click();
    await page.getByRole("list", { name: "Histórico de estados" }).waitFor().then(() => check(true, "WO detail with audit trail"), () => check(false, "WO detail with audit trail"));
    await page.goto(`${BASE}/dashboard`);
    await page.getByRole("heading", { name: "OS vencidas" }).waitFor({ timeout: 20_000 }).then(() => check(true, "Raio-X: overdue WOs card"), () => check(false, "Raio-X: overdue WOs card"));
    check(errors.length === 0, `zero console errors${errors.length ? ": " + errors.join(" | ") : ""}`);
    await context.close();
  }

  // ── technician phone ──
  console.log("\n[real] technician 360×740");
  {
    const tTok = await token("tecnico@factory-monitor.com", process.env.TECH_PASSWORD ?? "Tecnico@1234");
    const mine = (await api("GET", "/api/work-orders/?assignee_id=me&state=scheduled", tTok)).data;
    const target = mine.find((w) => w.task_list.length >= 2) ?? mine[0];
    check(!!target, `a scheduled WO assigned to the technician (${target?.number ?? "none"})`);
    if (target) {
      const { context, page, errors } = await newPage(browser, { width: 360, height: 740 }, true);
      await login(page, "tecnico@factory-monitor.com", process.env.TECH_PASSWORD ?? "Tecnico@1234");
      await page.waitForURL(/\/m\/work-orders/);
      check(true, "technician lands on /m/work-orders after login");
      await page.locator(`[data-wo="${target.number}"]`).click();
      await page.getByRole("button", { name: "Iniciar", exact: true }).click();
      await page.getByText("OS iniciada.").waitFor();
      await page.getByTestId("task-item").first().click();
      await page.waitForFunction(() => document.querySelector('[data-testid="task-item"]')?.getAttribute("aria-checked") === "true");
      await context.setOffline(true);
      await page.getByTestId("sync-status").filter({ hasText: "Sem conexão" }).waitFor();
      await page.getByTestId("task-item").nth(1).click();
      await page.getByRole("button", { name: "Apontar horas" }).click();
      await page.getByRole("dialog", { name: "Apontar horas" }).getByRole("button", { name: "Apontar" }).click();
      await page.getByTestId("sync-status").filter({ hasText: "2 pendentes" }).waitFor().then(() => check(true, "offline: 2 actions queued"), () => check(false, "offline: 2 actions queued"));
      await page.screenshot({ path: path.join(OUT, "m-offline.png") });
      for (let i = errors.length - 1; i >= 0; i--) if (/ERR_INTERNET_DISCONNECTED|WebSocket/.test(errors[i])) errors.splice(i, 1);
      await context.setOffline(false);
      await page.getByTestId("sync-status").filter({ hasText: "Online" }).waitFor({ timeout: 30_000 }).then(() => check(true, "back online: synced"), () => check(false, "back online: synced"));
      const after = (await api("GET", `/api/work-orders/${target.id}`, tTok)).data;
      check(after.state === "in_progress" && after.task_list.slice(0, 2).every((t) => t.done) && after.labor_hours >= 0.5,
        `server state after sync: ${after.state}, tasks ${after.task_list.map((t) => (t.done ? "x" : "-")).join("")}, labor ${after.labor_hours} h`);
      await page.screenshot({ path: path.join(OUT, "m-synced.png") });
      for (let i = errors.length - 1; i >= 0; i--) if (/ERR_INTERNET_DISCONNECTED|WebSocket/.test(errors[i])) errors.splice(i, 1);
      check(errors.length === 0, `zero console errors${errors.length ? ": " + errors.join(" | ") : ""}`);
      await context.close();
    }
  }
} catch (err) {
  failures += 1;
  console.log(`  ✗ step threw: ${String(err).split("\n")[0]}`);
} finally {
  await browser.close();
}
console.log(`\n${failures === 0 ? "ALL CHECKS PASSED" : `${failures} CHECK(S) FAILED`}`);
process.exit(failures === 0 ? 0 : 1);
