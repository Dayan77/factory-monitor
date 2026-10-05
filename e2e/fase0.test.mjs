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

test("dashboard is not blank and live readings show sensor names, not UUIDs", async () => {
  await page.getByRole("heading", { name: "Status das máquinas" }).waitFor({ timeout: 10_000 });
  await page.getByText("OEE disponível na Fase 2").waitFor();

  // Live readings arrive over the WebSocket; the simulator publishes every ~2 s.
  const live = page.locator('section[aria-labelledby="dash-live"]');
  await live.waitFor({ timeout: 20_000 });
  await live.locator("li").first().waitFor({ timeout: 20_000 });
  // give the tags query a moment to resolve names
  await page.waitForFunction(
    () => !document.querySelector('section[aria-labelledby="dash-live"]')?.textContent?.includes("Carregando"),
    null, { timeout: 10_000 },
  );
  const items = await live.locator("li").allInnerTexts();
  assert.ok(items.length > 0, "expected at least one live reading");
  for (const t of items) {
    assert.doesNotMatch(t, UUID_RE, `live reading shows a UUID: ${t}`);
    assert.doesNotMatch(t, /Sensor não cadastrado/, `live reading for an unknown tag: ${t}`);
  }

  const body = await page.locator("main").innerText();
  assert.ok(body.trim().length > 50, "dashboard main area looks blank");
  assert.doesNotMatch(body, UUID_RE, "dashboard shows a raw UUID");
  assert.doesNotMatch(body, /Não foi possível carregar/, "dashboard shows an error state");
  await shot("02-dashboard-live");
});

test("machines page shows status 'Operando' while the simulator runs", async () => {
  await page.goto(`${BASE}/machines`);
  const cards = page.getByTestId("machine-card");
  await cards.first().waitFor({ timeout: 10_000 });
  const n = await cards.count();
  assert.ok(n > 0, "no machine cards");
  // every machine fed by the simulator should be running within a few seconds
  await page.waitForFunction(
    () => [...document.querySelectorAll('[data-testid="machine-card"]')]
      .every((c) => c.textContent.includes("Operando")),
    null, { timeout: 20_000 },
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
