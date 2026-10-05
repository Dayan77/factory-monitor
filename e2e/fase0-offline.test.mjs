// E2E — Fase 0: machine status follows the data, live in the UI (no reload).
//
//   simulator running -> every card "Operando"
//   simulator stopped -> every card "Sem comunicação" within ~60 s offline timeout + 15 s sweep
//   simulator restarted -> back to "Operando" within a few seconds
//
// This spec STOPS and RESTARTS the edge simulator, so it is opt-in:
//
//   E2E_SIM_CONTROL=1 node --test e2e/fase0-offline.test.mjs
//
// It finds the simulator with pgrep (`python -m edge.src.simulator`) and restarts it
// with the same defaults as CLAUDE.md, detached, so it keeps running afterwards.
// Env: E2E_BASE_URL, E2E_EMAIL, E2E_PASSWORD, E2E_SIM_PYTHON (default .venv-edge/bin/python),
//      E2E_SIM_ARGS (default "--server http://localhost:8000 --broker localhost").
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { execSync, spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ENABLED = process.env.E2E_SIM_CONTROL === "1";
const BASE = process.env.E2E_BASE_URL ?? "http://localhost:5173";
const EMAIL = process.env.E2E_EMAIL ?? "admin@factory-monitor.com";
const PASSWORD = process.env.E2E_PASSWORD ?? "Admin@1234";
const DEFAULT_PW = "/opt/node-tools/node_modules/playwright/index.mjs";
const PW_MODULE = process.env.E2E_PW_MODULE ?? (existsSync(DEFAULT_PW) ? DEFAULT_PW : "playwright");
const CHROMIUM = process.env.E2E_CHROMIUM ?? (existsSync("/opt/pw-browsers/chromium") ? "/opt/pw-browsers/chromium" : undefined);
const SIM_PY = process.env.E2E_SIM_PYTHON ?? path.join(ROOT, ".venv-edge/bin/python");
const SIM_ARGS = (process.env.E2E_SIM_ARGS ?? "--server http://localhost:8000 --broker localhost").split(" ");
// Anchored so it never matches the shell/node process running this test.
const SIM_PATTERN = "^[^ ]*python[0-9.]* -m edge[.]src[.]simulator";

const OFFLINE_BUDGET_MS = 95_000; // 60 s timeout + 15 s sweep + margin
let browser, page;
const consoleErrors = [];

function simPids() {
  try { return execSync(`pgrep -f '${SIM_PATTERN}'`).toString().trim().split("\n").filter(Boolean); }
  catch { return []; }
}
function startSim() {
  const p = spawn(SIM_PY, ["-m", "edge.src.simulator", ...SIM_ARGS], { cwd: ROOT, detached: true, stdio: "ignore" });
  p.unref();
}
const allCards = (text, timeout) => page.waitForFunction(
  (t) => {
    const c = [...document.querySelectorAll('[data-testid="machine-card"]')];
    return c.length > 0 && c.every((x) => x.textContent.includes(t));
  }, text, { timeout },
);

before(async () => {
  if (!ENABLED) return;
  const { chromium } = await import(PW_MODULE);
  browser = await chromium.launch({ executablePath: CHROMIUM });
  page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
  page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));
});

after(async () => {
  if (ENABLED && simPids().length === 0) startSim(); // never leave the dev stack without data
  await browser?.close();
});

test("machine cards go offline when the simulator stops and back when it restarts", { skip: !ENABLED && "set E2E_SIM_CONTROL=1" }, async () => {
  if (simPids().length === 0) startSim();
  await page.goto(`${BASE}/login`);
  await page.fill('input[type="email"]', EMAIL);
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/dashboard/, { timeout: 15_000 });
  await page.goto(`${BASE}/machines`);
  await page.getByTestId("machine-card").first().waitFor({ timeout: 10_000 });
  await allCards("Operando", 30_000);

  execSync(`kill ${simPids().join(" ")}`);
  const t0 = Date.now();
  await allCards("Sem comunicação", OFFLINE_BUDGET_MS);
  const offlineS = (Date.now() - t0) / 1000;
  assert.ok(offlineS >= 45, `went offline too early (${offlineS}s) — offline timeout is 60 s`);

  startSim();
  const t1 = Date.now();
  await allCards("Operando", 30_000);
  assert.ok((Date.now() - t1) / 1000 < 30);
  assert.deepEqual(consoleErrors, [], `console errors:\n${consoleErrors.join("\n")}`);
});
