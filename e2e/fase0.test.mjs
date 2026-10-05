// E2E — Fase 0 (Sprint 1) acceptance flow.
//
//   login -> dashboard (renders, live readings show sensor names, never UUIDs)
//         -> machines (status "Operando" while the simulator publishes)
//         -> alerts (renders list or empty state, no error state)
//
// Prerequisites: backend (:8000), vite (:5173) and the edge simulator running
// (see CLAUDE.md / `make dev`). Run with:
//
//   node --test e2e/*.test.mjs
//
// Env overrides:
//   E2E_BASE_URL       default http://localhost:5173
//   E2E_EMAIL          default admin@factory-monitor.com
//   E2E_PASSWORD       default Admin@1234 (dev seed)
//   E2E_PW_MODULE      path to playwright's index.mjs (default: /opt/node-tools/... or "playwright")
//   E2E_CHROMIUM       chromium executable (default: /opt/pw-browsers/chromium if present)
//   E2E_SHOTS_DIR      where screenshots go (default: e2e/screenshots, git-ignored by caller)
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BASE = process.env.E2E_BASE_URL ?? "http://localhost:5173";
const EMAIL = process.env.E2E_EMAIL ?? "admin@factory-monitor.com";
const PASSWORD = process.env.E2E_PASSWORD ?? "Admin@1234";
const DEFAULT_PW = "/opt/node-tools/node_modules/playwright/index.mjs";
const PW_MODULE = process.env.E2E_PW_MODULE ?? (existsSync(DEFAULT_PW) ? DEFAULT_PW : "playwright");
const DEFAULT_CHROMIUM = "/opt/pw-browsers/chromium";
const CHROMIUM = process.env.E2E_CHROMIUM ?? (existsSync(DEFAULT_CHROMIUM) ? DEFAULT_CHROMIUM : undefined);
const SHOTS = process.env.E2E_SHOTS_DIR ?? path.join(HERE, "screenshots");

const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

let browser, context, page;
const consoleErrors = [];
const failedRequests = [];

before(async () => {
  mkdirSync(SHOTS, { recursive: true });
  const { chromium } = await import(PW_MODULE);
  browser = await chromium.launch({ executablePath: CHROMIUM });
  context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  page = await context.newPage();
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
  page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));
  page.on("response", (r) => {
    if (r.status() >= 400 && r.url().includes("/api/")) failedRequests.push(`${r.status()} ${r.url()}`);
  });
});

after(async () => {
  await browser?.close();
});

async function shot(name) {
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
}

test("login with the dev admin lands on the dashboard", async () => {
  await page.goto(`${BASE}/login`);
  await page.fill('input[type="email"]', EMAIL);
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/dashboard/, { timeout: 15_000 });
  await shot("01-dashboard-initial");
});

// Sprint 3: /dashboard is the Raio-X (the old dashboard and its "Leituras ao Vivo" box were removed);
// live readings are shown on /sensors ("Sensores ao Vivo"), labelled by tag name.
test("Raio-X home is not blank, and /sensors shows live readings by name, not UUIDs", async () => {
  await page.getByTestId("line-card").first().waitFor({ timeout: 20_000 });
  const body = await page.locator("main").innerText();
  assert.ok(body.trim().length > 50, "Raio-X main area looks blank");
  assert.doesNotMatch(body, UUID_RE, "Raio-X shows a raw UUID");
  assert.doesNotMatch(body, /Não foi possível carregar/, "Raio-X shows an error state");
  await shot("02-raiox");

  await page.goto(`${BASE}/sensors`);
  await page.getByRole("heading", { name: "Sensores ao Vivo" }).waitFor({ timeout: 10_000 });
  // the simulator publishes every ~2 s over MQTT → WebSocket
  await page.locator("main p.font-mono").first().waitFor({ timeout: 20_000 });
  // sensor NAMES (not the "PROTOCOL · address" line, whose MQTT address legitimately embeds an id)
  const names = await page.locator("main p.text-sm.truncate").allInnerTexts();
  assert.ok(names.length > 0, "no sensor names listed");
  for (const n of names) assert.doesNotMatch(n, UUID_RE, `sensor/machine name is a raw UUID: ${n}`);
  await shot("02-sensors-live");
});

test("machines page shows status 'Operando' while the simulator runs", async () => {
  await page.goto(`${BASE}/machines`);
  // only the machines the simulator feeds (its own line); the seeded virtual-plant machines are fed by the
  // virtual plant, which may not be running. E2E_SIM_LINE="*" checks every card.
  const simLine = process.env.E2E_SIM_LINE ?? "Linha Demo (Simulador)";
  const all = page.getByTestId("machine-card");
  await all.first().waitFor({ timeout: 10_000 });
  const cards = simLine === "*" ? all : all.filter({ hasText: simLine });
  const n = await cards.count();
  assert.ok(n > 0, "no machine cards");
  // every machine fed by the simulator should be running within a few seconds
  await page.waitForFunction(
    (line) => [...document.querySelectorAll('[data-testid="machine-card"]')]
      .filter((c) => line === "*" || c.textContent.includes(line))
      .every((c) => c.textContent.includes("Operando")),
    simLine, { timeout: 20_000 },
  ).catch(() => {});
  const texts = await cards.allInnerTexts();
  const running = texts.filter((t) => t.includes("Operando"));
  assert.equal(running.length, n, `not all machines are running:\n${texts.join("\n---\n")}`);
  for (const t of texts) {
    assert.match(t, /visto há \d+ ?s/, `card has no recent 'visto há': ${t}`);
    assert.doesNotMatch(t, UUID_RE);
  }
  await shot("03-machines");
});

test("alerts page renders a list or the empty state, never an error", async () => {
  await page.goto(`${BASE}/alerts`);
  await page.getByRole("group", { name: "Filtrar alertas" }).waitFor({ timeout: 10_000 });
  await page.locator("text=/\\d+ alertas|Nenhum alerta encontrado/").first().waitFor({ timeout: 10_000 });
  const body = await page.locator("main").innerText();
  assert.doesNotMatch(body, /Não foi possível carregar|respondeu com erro/);
  assert.doesNotMatch(body, UUID_RE, "alerts page shows a raw UUID");
  await shot("04-alerts");
});

test("no console errors and no failed API requests during the flow", () => {
  assert.deepEqual(consoleErrors, [], `console errors:\n${consoleErrors.join("\n")}`);
  assert.deepEqual(failedRequests, [], `failed API requests:\n${failedRequests.join("\n")}`);
});
