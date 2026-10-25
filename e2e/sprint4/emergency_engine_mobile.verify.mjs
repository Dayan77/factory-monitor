/**
 * Sprint 4 acceptance §7 item 7 — UI half for the AUTOMATIC emergency call (engine path, no supervisor tap).
 * Start this BEFORE forcing the robot failure (emergency_live.py call): the technician's phone (360x740) is open
 * on /m; the engine's emergency_call must arrive over the WebSocket as the full-screen alert, whose action opens
 * /m/work-orders/<id> of the engine-created emergency WO; "Assumir chamado" (open → in_progress); the ISO 14224
 * failure mode is recorded from the phone. Writes {woId, failureId} to STATE for PHASE=complete of
 * emergency_mobile.verify.mjs.
 *
 *   BASE_URL=http://localhost:5220 API=http://localhost:8120 node e2e/sprint4/emergency_engine_mobile.verify.mjs
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import fs from "node:fs";

const BASE = process.env.BASE_URL ?? "http://localhost:5220";
const API = process.env.API ?? "http://localhost:8120";
const OUT = process.env.OUT ?? "e2e/screenshots/sprint4";
const STATE = process.env.STATE ?? `${OUT}/emergency_state.json`;
const WAIT_MS = Number(process.env.WAIT_MS ?? 420_000);
fs.mkdirSync(OUT, { recursive: true });
let failures = 0;
const check = (c, m, x) => { if (c) console.log(`  ✓ ${m}`); else { failures++; console.log(`  ✗ ${m}${x !== undefined ? ` — got ${JSON.stringify(x)}` : ""}`); } };
async function api(method, p, token, body) {
  const r = await fetch(`${API}${p}`, { method, headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, data: await r.json().catch(() => null) };
}
const planner = (await api("POST", "/api/auth/login", null, { email: "planejador@factory-monitor.com", password: "Planner@1234" })).data.access_token;

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
try {
  const context = await browser.newContext({ viewport: { width: 360, height: 740 }, hasTouch: true, isMobile: true, timezoneId: "America/Manaus", locale: "pt-BR" });
  const page = await context.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(`${BASE}/login`);
  await page.getByLabel(/e-mail/i).fill("tecnico@factory-monitor.com");
  await page.getByLabel(/senha/i).fill("Tecnico@1234");
  await page.getByRole("button", { name: /entrar/i }).click();
  await page.waitForURL(/\/m(\/|$)/, { timeout: 20_000 });
  check(true, `technician lands on ${new URL(page.url()).pathname}`);
  fs.writeFileSync(`${OUT}/engine_phone_ready`, String(Date.now()));
  console.log("  … phone ready, waiting for the engine's emergency call");

  const alert = page.getByTestId("emergency-alert");
  let shown = true;
  try { await alert.waitFor({ timeout: WAIT_MS }); } catch { shown = false; }
  check(shown, "phone: full-screen emergency alert arrived over the WebSocket (engine call, nobody tapped anything)");
  if (!shown) throw new Error("no alert");
  await page.screenshot({ path: `${OUT}/e7-engine-phone-alert.png` });
  const atext = (await alert.innerText()).replace(/\s+/g, " ");
  console.log(`    alert: ${atext.slice(0, 200)}`);
  check(atext.includes("Robô de Solda"), "alert names the robot");
  await alert.getByRole("button").first().click();
  await page.waitForTimeout(2000);
  const afterAlert = new URL(page.url()).pathname;
  console.log(`    alert action → ${afterAlert}`);
  check(/^\/m\/work-orders\/[^/]+$/.test(afterAlert) || afterAlert === "/m/work-orders", "alert action opens the WO (or the /m list) on the phone", afterAlert);

  await page.goto(`${BASE}/m/work-orders`);
  const card = page.getByTestId("emergency-call").filter({ hasText: "Robô de Solda" }).first();
  await card.waitFor({ timeout: 20_000 });
  await page.screenshot({ path: `${OUT}/e7-engine-phone-card.png`, fullPage: true });
  await card.getByRole("button", { name: "Assumir chamado" }).click();
  await page.waitForURL(/\/m\/work-orders\/[^/]+$/, { timeout: 15_000 });
  const woId = page.url().split("/").pop();
  if (/^\/m\/work-orders\/[^/]+$/.test(afterAlert)) check(afterAlert.endsWith(woId), "alert linked to the same WO the technician took", { afterAlert, woId });
  await page.getByText("Em execução").first().waitFor({ timeout: 10_000 });
  const wo = (await api("GET", `/api/work-orders/${woId}`, planner)).data;
  const hist = wo.history.map((h) => h.to_state).join(">");
  check(wo.state === "in_progress" && hist === "open>in_progress", `${wo.number} open → in_progress (emergency shortcut)`, hist);
  check(wo.urgency === "emergency" && wo.type === "corrective" && wo.failure_id && wo.created_by == null, `engine-created emergency corrective WO linked to failure ${wo.failure_id}`, { urgency: wo.urgency, created_by: wo.created_by });
  check(wo.assignee_id, "technician is now the assignee");

  await page.getByRole("button", { name: "Informar" }).click();
  const sheet = page.getByRole("dialog");
  await sheet.getByRole("radiogroup", { name: "Modo de falha" }).waitFor({ timeout: 15_000 });
  const pos = sheet.locator("select").first();
  if ((await pos.locator("option").allInnerTexts()).some((o) => /Eixo 1/.test(o))) await pos.selectOption({ label: "Eixo 1" });
  await sheet.getByRole("radiogroup", { name: "Modo de falha" }).getByRole("radio", { name: /BRD/ }).click().catch(async () => {
    await sheet.getByRole("button", { name: "Ver todos os modos" }).click();
    await sheet.getByRole("radiogroup", { name: "Modo de falha" }).getByRole("radio", { name: /BRD/ }).click();
  });
  await page.screenshot({ path: `${OUT}/e7-engine-phone-failure-form.png`, fullPage: true });
  await sheet.getByRole("button", { name: "Salvar dados da falha" }).click();
  await page.getByTestId("exec-notice").filter({ hasText: /Dados da falha salvos/ }).waitFor({ timeout: 10_000 });
  const f = (await api("GET", `/api/failures/${wo.failure_id}`, planner)).data;
  check(f.failure_mode === "BRD", "failure mode BRD saved from the phone", f.failure_mode);
  check(f.restored_at === null, "failure still open until the machine runs again");
  check(!(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)), "no horizontal overflow at 360 px");
  check(errors.length === 0, "phone: no console errors", errors);
  fs.writeFileSync(STATE, JSON.stringify({ woId, failureId: wo.failure_id }));
} catch (e) {
  failures++; console.log(`  ✗ ${e}`);
} finally {
  await browser.close();
}
console.log(failures ? `\n${failures} CHECK(S) FAILED` : "\nALL CHECKS PASSED");
process.exit(failures ? 1 : 0);
