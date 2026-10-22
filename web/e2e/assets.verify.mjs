/**
 * Browser verification of the asset & usage screens (Fábrica → Linha → Máquina → Posição) and the
 * landing contact form.
 *
 *   mocked mode (default): every /api call and the realtime WebSocket are served by page.route /
 *     page.routeWebSocket with clearly fictitious fixture data — deterministic screenshots in
 *     light/dark × desktop/mobile, live "usage" increments over the socket, zero console errors.
 *   real mode (MODE=real): drives the running stack (login + real API), including the admin
 *     actions register → remove → install on a position.
 *
 *   BASE_URL=http://localhost:5173 OUT=./shots node e2e/assets.verify.mjs
 *   MODE=real BASE_URL=http://localhost:5173 node e2e/assets.verify.mjs
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.BASE_URL ?? "http://localhost:5173";
const OUT = process.env.OUT ?? "e2e/screenshots";
const MODE = process.env.MODE ?? "mocked";
fs.mkdirSync(OUT, { recursive: true });

const VIEWPORTS = { desktop: { width: 1440, height: 900 }, mobile: { width: 390, height: 844 } };
let failures = 0;
function check(cond, msg) {
  if (cond) console.log(`  ✓ ${msg}`);
  else { failures += 1; console.log(`  ✗ ${msg}`); }
}

// ── fixtures (mocked mode) ──────────────────────────────────────────
const NOW = Date.now();
const hourIso = (h) => new Date(Math.floor(NOW / 3600e3) * 3600e3 - h * 3600e3).toISOString();
const TREE = [{
  id: "plant-1", name: "Planta Demo", timezone: "America/Manaus",
  lines: [
    { id: "line-1", name: "Linha de Montagem", status_summary: { running: 2, stopped: 0, error: 1, unknown: 0, maintenance: 0 },
      machines: [
        { id: "m-1", name: "Estação de Fixação", status: "running", last_seen_at: new Date(NOW - 4000).toISOString(), positions_count: 4 },
        { id: "m-2", name: "Robô de Solda", status: "error", last_seen_at: new Date(NOW - 9000).toISOString(), positions_count: 6 },
        { id: "m-3", name: "Transportador de Saída", status: "running", last_seen_at: new Date(NOW - 2000).toISOString(), positions_count: 1 },
      ] },
    { id: "line-2", name: "Linha de Usinagem", status_summary: { running: 1, stopped: 1, error: 0, unknown: 0, maintenance: 0 },
      machines: [
        { id: "m-4", name: "Centro de Usinagem", status: "running", last_seen_at: new Date(NOW - 3000).toISOString(), positions_count: 3 },
        { id: "m-5", name: "Torno CNC", status: "stopped", last_seen_at: new Date(NOW - 60000).toISOString(), positions_count: 3 },
      ] },
  ],
}];
const USAGE = {
  machine_id: "m-1",
  production: { good: 1843, scrap: 37, total: 1880, timezone: "America/Manaus" },
  positions: [
    { position_id: "p-1", name: "Cilindro A1", component_type: "pneumatic_cylinder", cycles_total: 3_712_440, hours_total: 1210.4, starts_total: 52, component_id: "c-1", life_consumed_pct: 0.464, life_measure: "cycles" },
    { position_id: "p-2", name: "Cilindro A2", component_type: "pneumatic_cylinder", cycles_total: 6_120_330, hours_total: 1210.4, starts_total: 52, component_id: "c-2", life_consumed_pct: 0.765, life_measure: "cycles" },
    { position_id: "p-3", name: "Motor do fuso", component_type: "electric_motor", cycles_total: 0, hours_total: 28_100.2, starts_total: 9_420, component_id: "c-3", life_consumed_pct: 0.937, life_measure: "hours" },
    { position_id: "p-4", name: "Garra pneumática", component_type: "gripper", cycles_total: 1_880, hours_total: 12.5, starts_total: 3, component_id: null, life_consumed_pct: null, life_measure: null, cycles_estimated: true },
  ],
};
const POSITION = {
  id: "p-2", machine_id: "m-1", name: "Cilindro A2", component_type: "pneumatic_cylinder", cycles_per_part: 1,
  installed_component: { id: "c-2", position_id: "p-2", component_type: "pneumatic_cylinder", manufacturer: "Fabricante X", model: "CIL-50-100", serial_number: "DEMO-0002", installed_at: "2026-03-02T11:00:00Z", removed_at: null, removal_reason: null, nominal_life: { cycles: 8_000_000 }, state: "installed", life_consumed_pct: 0.765, life_measure: "cycles" },
  usage: { cycles_total: 9_870_000, hours_total: 3010.2, starts_total: 140 },
  component_usage: { cycles: 6_120_330, hours: 1210.4, starts: 52, life_consumed_pct: 0.765, life_measure: "cycles", projected_end_date: "2026-11-21", rul_days: 47, low_confidence: false, overdue: false },
};
const HISTORY = [
  POSITION.installed_component,
  { id: "c-0", position_id: null, last_position_id: "p-2", component_type: "pneumatic_cylinder", manufacturer: "Fabricante X", model: "CIL-50-100", serial_number: "DEMO-0001", installed_at: "2025-06-10T09:00:00Z", removed_at: "2026-03-02T10:30:00Z", removal_reason: "failure", nominal_life: { cycles: 8_000_000 }, state: "removed", life_consumed_pct: 0.468, life_measure: "cycles" },
];
const STOCK = [{ id: "c-9", position_id: null, component_type: "pneumatic_cylinder", manufacturer: "Fabricante X", model: "CIL-50-100", serial_number: "DEMO-0009", installed_at: null, nominal_life: { cycles: 8_000_000 }, state: "in_stock" }];
const wave = (h) => Math.round(70 + 25 * Math.sin(h / 2.3) + (h % 5));
const PROD_POINTS = Array.from({ length: 24 }, (_, i) => 23 - i).map((h) => ({ t: hourIso(h), good: h === 6 ? 0 : wave(h), scrap: h === 6 ? 0 : (h % 4 === 0 ? 3 : 1) }));
const sum = (k) => PROD_POINTS.reduce((s, p) => s + p[k], 0);
const LINE_PROD = { line_id: "line-1", start: hourIso(23), stop: new Date(NOW).toISOString(), good: sum("good"), scrap: sum("scrap"), total: sum("good") + sum("scrap"), cadence_per_hour: (sum("good") + sum("scrap")) / 24, points: PROD_POINTS };
const USAGE_HIST = { start: hourIso(23), stop: new Date(NOW).toISOString(), points: PROD_POINTS.map((p) => ({ t: p.t, value: p.good })) };

async function mockApi(page) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    const json = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (p === "/api/auth/me") return json({ id: "u1", name: "Admin Demo", email: "admin@example.com", role: "admin", tenant_id: "t-demo" });
    if (p === "/api/plants/tree") return json(TREE);
    if (/^\/api\/machines\/[^/]+\/usage$/.test(p)) return json(USAGE);
    if (p === "/api/usage/history") return json(USAGE_HIST);
    if (/^\/api\/lines\/[^/]+\/production$/.test(p)) return json(LINE_PROD);
    if (/^\/api\/positions\/[^/]+$/.test(p)) return json(POSITION);
    if (p === "/api/components/") return json(url.searchParams.get("in_stock") === "true" ? STOCK : HISTORY);
    if (p === "/api/public/leads") return json({ status: "accepted" }, 202);
    return json([]);
  });
}

async function newPage(browser, { theme, viewport }) {
  const context = await browser.newContext({ viewport, colorScheme: theme, deviceScaleFactor: 1 });
  const page = await context.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  return { context, page, errors };
}

async function shot(page, name) {
  await page.waitForTimeout(350); // let transitions settle
  // the app scrolls inside <main>: grow the viewport so the full page is captured
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
      const { context, page, errors } = await newPage(browser, { theme, viewport });
      await mockApi(page);
      let socket = null;
      await page.routeWebSocket(/\/ws\/sensors\//, (ws) => { socket = ws; });
      await page.addInitScript((t) => {
        localStorage.setItem("access_token", "fake.jwt.token");
        localStorage.setItem("fm-theme", t);
      }, theme);

      await page.goto(`${BASE}/factory/list`); // /factory is the plant map since sprint 6
      await page.getByTestId("line-card").first().waitFor();
      check((await page.getAttribute("html", "data-theme")) === theme, `html data-theme=${theme}`);
      check((await page.getByTestId("line-card").first().textContent()).includes("Linha de Montagem"), "line with failure listed first");
      await shot(page, `factory-${theme}-${vpName}`);

      await page.getByTestId("line-card").first().click();
      await page.getByText("Cadência média").waitFor();
      // the drill-down keeps the period in the query since sprint 6 (?period=…): compare the path
      check(new URL(page.url()).pathname === "/factory/lines/line-1", "drill-down to /factory/lines/:id", page.url());
      const fill = await page.locator(".recharts-bar-rectangle path, .recharts-bar-rectangle rect").first().evaluate((el) => getComputedStyle(el).fill);
      check(fill && fill !== "rgb(0, 0, 0)" && fill !== "none", `chart bars resolve token colour (${fill})`);
      await shot(page, `line-${theme}-${vpName}`);

      await page.getByTestId("machine-tile").first().click();
      await page.getByRole("heading", { name: "Estação de Fixação" }).waitFor();
      await page.getByText("Motor do fuso").filter({ visible: true }).first().waitFor();
      check(new URL(page.url()).pathname === "/factory/machines/m-1", "drill-down to /factory/machines/:id", page.url());
      await page.waitForTimeout(300);
      // live usage over the WebSocket
      if (socket) {
        socket.send(JSON.stringify({ type: "usage", position_id: "p-1", machine_id: "m-1", measure: "cycles", delta: 4, total: 3_712_444 }));
        socket.send(JSON.stringify({ type: "usage", position_id: null, machine_id: "m-1", measure: "total", delta: 2, total: 99999 }));
        await page.getByText("3.712.444").filter({ visible: true }).first().waitFor({ timeout: 3000 }).then(
          () => check(true, "live usage increment applied (cycles 3.712.440 → 3.712.444)"),
          () => check(false, "live usage increment applied"),
        );
        check(await page.getByText("1.845").first().isVisible().catch(() => false), "live production increment (good 1.843 → 1.845)");
      } else {
        check(false, "realtime socket opened");
      }
      await shot(page, `machine-${theme}-${vpName}`);

      const opener = vpName === "desktop"
        ? page.getByRole("row", { name: "Abrir posição Cilindro A2" })
        : page.getByRole("button", { name: /Cilindro A2/ });
      await opener.click();
      const dialog = page.getByRole("dialog");
      await dialog.getByText("DEMO-0002").first().waitFor();
      check(new URL(page.url()).searchParams.get("pos") === "p-2", "drawer state in URL (?pos=)", page.url());
      check(await dialog.getByText("21/11/2026").isVisible(), "projected end date shown");
      await shot(page, `drawer-${theme}-${vpName}`);
      await page.keyboard.press("Escape");
      await dialog.waitFor({ state: "detached" });
      check(true, "drawer closes with Escape");

      // theme toggle in the header
      await page.getByRole("button", { name: theme === "dark" ? "Ativar tema claro" : "Ativar tema escuro" }).click();
      check((await page.getAttribute("html", "data-theme")) !== theme, "theme toggle switches theme");

      // landing contact
      await page.goto(`${BASE}/`);
      check((await page.getByText("Metalúrgica Fonseca").count()) === 0, "no fictional cases on landing");
      await page.locator("#contact").scrollIntoViewIfNeeded();
      const form = page.getByRole("form", { name: "Formulário de contato" });
      await form.getByLabel(/Seu nome/).fill("Pessoa Teste");
      await form.getByLabel(/E-mail/).fill("teste@example.com");
      await form.getByLabel(/Mensagem/).fill("Gostaria de uma demonstração.");
      await shot(page.locator("#contact").page(), `landing-${theme}-${vpName}`);
      await form.getByRole("checkbox").check();
      const [req] = await Promise.all([
        page.waitForRequest((r) => r.url().endsWith("/api/public/leads") && r.method() === "POST"),
        form.getByRole("button", { name: "Enviar mensagem" }).click(),
      ]);
      const body = req.postDataJSON();
      check(body.consent === true && body.website === "" && body.email === "teste@example.com", "lead POST body (consent, honeypot empty)");
      await page.getByText("Mensagem enviada!").waitFor();
      check(true, "lead success state");

      check(errors.length === 0, `zero console errors${errors.length ? ": " + errors.join(" | ") : ""}`);
      await context.close();
    }
  }
}

async function runReal(browser) {
  console.log("\n[real] admin flow against the running API");
  const { context, page, errors } = await newPage(browser, { theme: "dark", viewport: VIEWPORTS.desktop });
  await page.goto(`${BASE}/login`);
  await page.getByLabel(/e-mail/i).fill(process.env.E2E_EMAIL ?? "admin@factory-monitor.com");
  await page.getByLabel(/senha/i).fill(process.env.E2E_PASSWORD ?? "Admin@1234");
  await page.getByRole("button", { name: /entrar/i }).click();
  await page.waitForURL(/dashboard/);
  await page.goto(`${BASE}/factory/list`);
  await page.getByTestId("line-card").first().waitFor();
  check((await page.getByTestId("line-card").count()) > 0, "tree loaded from /api/plants/tree");
  await shot(page, "real-factory");
  await page.getByTestId("line-card").first().click();
  await page.getByRole("heading", { level: 2 }).first().waitFor();
  await page.getByText(/Cadência média|Produção indisponível|Sem produção/).first().waitFor();
  await shot(page, "real-line");
  await page.getByTestId("machine-tile").first().click();
  const firstRow = page.getByTestId("position-row").first();
  await firstRow.waitFor();
  check((await page.getByTestId("position-row").count()) > 0, "positions from /api/machines/{id}/usage");
  await shot(page, "real-machine");
  await firstRow.click();
  const dialog = page.getByRole("dialog");
  await dialog.getByText("Componente instalado", { exact: true }).waitFor();
  await dialog.getByRole("heading", { level: 3 }).first().waitFor();
  await page.waitForTimeout(500);
  await shot(page, "real-drawer");

  const serial = `WEB-E2E-${Date.now()}`;
  // register a spare (stock) component
  await dialog.getByRole("button", { name: "Cadastrar novo componente" }).click();
  await dialog.getByLabel("Fabricante").fill("Fabricante Teste");
  await dialog.getByLabel("Modelo").fill("Modelo E2E");
  await dialog.getByLabel("Número de série").fill(serial);
  await dialog.getByLabel("Ciclos").fill("1000000");
  // keep it in stock even if a previous run left this position empty (the box defaults to on then)
  const installNow = dialog.getByLabel("Instalar nesta posição agora");
  if (await installNow.count()) await installNow.uncheck();
  await dialog.getByRole("button", { name: "Cadastrar" }).click();
  await dialog.getByRole("button", { name: "Cadastrar novo componente" }).waitFor();
  check(true, "component registered (POST /api/components/)");
  // remove the installed one, if any (a previous run may have left this position empty)
  let removed = false;
  if (await dialog.getByRole("button", { name: "Remover" }).count()) {
    await dialog.getByRole("button", { name: "Remover" }).click();
    await dialog.getByLabel("Motivo da remoção").selectOption("preventive_replacement");
    await dialog.getByRole("button", { name: "Confirmar remoção" }).click();
    await dialog.getByText("Nenhum componente instalado").waitFor();
    check(true, "installed component removed (POST /api/components/{id}/remove)");
    removed = true;
  }
  await dialog.getByRole("button", { name: "Instalar do estoque" }).click();
  const select = dialog.getByLabel("Componente em estoque");
  await select.waitFor();
  const value = await select.locator("option", { hasText: serial }).getAttribute("value");
  await select.selectOption(value);
  await dialog.getByRole("button", { name: "Instalar" }).click();
  await dialog.getByText(serial).first().waitFor();
  check(true, "spare installed from stock (POST /api/positions/{id}/install)");
  if (removed) check(await dialog.getByText("Troca preventiva").first().isVisible(), "history shows the removal reason");
  await shot(page, "real-drawer-after-swap");
  check(errors.length === 0, `zero console errors${errors.length ? ": " + errors.join(" | ") : ""}`);
  await context.close();
}

const browser = await chromium.launch({ executablePath: process.env.CHROMIUM ?? "/opt/pw-browsers/chromium" });
try {
  if (MODE === "real") await runReal(browser);
  else await runMocked(browser);
} catch (e) {
  failures += 1;
  console.error("✗ aborted:", e);
} finally {
  await browser.close();
}
console.log(failures ? `\n${failures} check(s) FAILED` : "\nall checks passed");
process.exit(failures ? 1 : 0);
