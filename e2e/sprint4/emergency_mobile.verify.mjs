/**
 * Sprint 4 acceptance §7 item 7 — the UI half, against the live stack of e2e/sprint4/emergency_live.py
 * (API with the virtual plant + gateway; vite proxying to it).
 *
 *   PHASE=call     supervisor (tablet) taps "Chamar manutenção" on the open robot stop while the technician's
 *                  phone (360x740) is open → full-screen emergency alert arrives over the WebSocket; technician
 *                  "Assumir chamado" (open → in_progress) and records the ISO 14224 failure mode.
 *   PHASE=complete technician completes the WO on the phone (after the plant repair).
 *
 *   BASE_URL=http://localhost:5220 API=http://localhost:8120 PHASE=call node e2e/sprint4/emergency_mobile.verify.mjs
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import fs from "node:fs";

const BASE = process.env.BASE_URL ?? "http://localhost:5220";
const API = process.env.API ?? "http://localhost:8120";
const PHASE = process.env.PHASE ?? "call";
const OUT = process.env.OUT ?? "e2e/screenshots/sprint4";
const STATE = process.env.STATE ?? `${OUT}/emergency_state.json`;
fs.mkdirSync(OUT, { recursive: true });
let failures = 0;
function check(cond, msg, extra) {
  if (cond) console.log(`  ✓ ${msg}`);
  else { failures += 1; console.log(`  ✗ ${msg}${extra !== undefined ? ` — got ${JSON.stringify(extra)}` : ""}`); }
}
async function api(method, p, token, body) {
  const r = await fetch(`${API}${p}`, { method, headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, data: await r.json().catch(() => null) };
}
const token = async (email, password) => (await api("POST", "/api/auth/login", null, { email, password })).data.access_token;
async function newPage(browser, viewport, mobile) {
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
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20_000 });
}

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const planner = await token("planejador@factory-monitor.com", "Planner@1234");
try {
  if (PHASE === "call") {
    const tech = await newPage(browser, { width: 360, height: 740 }, true);
    await login(tech.page, "tecnico@factory-monitor.com", "Tecnico@1234");
    await tech.page.waitForURL(/\/m\//);
    await tech.page.waitForTimeout(2000);

    const sup = await newPage(browser, { width: 1024, height: 768 }, true);
    await login(sup.page, "supervisor@factory-monitor.com", "Supervisor@1234");
    await sup.page.goto(`${BASE}/stops`);
    const btn = sup.page.getByRole("region", { name: "Paradas sem justificativa" }).locator("li").filter({ hasText: "Robô de Solda" }).getByTestId("call-maintenance").first();
    await btn.waitFor({ timeout: 20_000 });
    await sup.page.screenshot({ path: `${OUT}/e7-supervisor-stops.png`, fullPage: true });
    await btn.click();
    const st = sup.page.getByRole("status").filter({ hasText: /Manutenção chamada/ });
    await st.waitFor({ timeout: 15_000 });
    const msg = await st.innerText();
    check(/Manutenção chamada: OS-\d+/.test(msg), `supervisor: "${msg}"`);
    const woNumber = /OS-\d+/.exec(msg)?.[0];
    check(sup.errors.length === 0, "supervisor: no console errors", sup.errors);

    // technician phone: full-screen emergency alert over the WebSocket
    const alert = tech.page.getByTestId("emergency-alert");
    let alertShown = true;
    try { await alert.waitFor({ timeout: 20_000 }); } catch { alertShown = false; }
    check(alertShown, "phone: full-screen emergency alert (WebSocket) shown");
    if (alertShown) {
      await tech.page.screenshot({ path: `${OUT}/e7-phone-emergency-alert.png` });
      console.log(`    alert: ${(await alert.innerText()).replace(/\s+/g, " ").slice(0, 160)}`);
      check((await alert.innerText()).includes("Robô de Solda"), "alert names the robot");
      await alert.getByRole("button").first().click();
      await tech.page.waitForTimeout(1500);
      console.log(`    alert action → ${tech.page.url()}`);
    }
    await tech.page.goto(`${BASE}/m/work-orders`);
    const card = tech.page.getByTestId("emergency-call").filter({ hasText: "Robô de Solda" }).first();
    await card.waitFor({ timeout: 15_000 });
    await tech.page.screenshot({ path: `${OUT}/e7-phone-emergency-card.png`, fullPage: true });
    await card.getByRole("button", { name: "Assumir chamado" }).click();
    await tech.page.waitForURL(/\/m\/work-orders\/[^/]+$/, { timeout: 15_000 });
    const woId = tech.page.url().split("/").pop();
    await tech.page.getByText("Em execução").first().waitFor({ timeout: 10_000 });
    const wo = (await api("GET", `/api/work-orders/${woId}`, planner)).data;
    check(wo.state === "in_progress" && wo.history.map((h) => h.to_state).join(">") === "open>in_progress", `${wo.number} open → in_progress (emergency shortcut), number matches ${woNumber}`, wo.history?.map((h) => h.to_state));
    check(wo.number === woNumber && wo.urgency === "emergency" && wo.failure_id, `WO linked to the failure (${wo.failure_id})`);

    // ISO 14224 failure data from the phone
    await tech.page.getByRole("button", { name: "Informar" }).click();
    const sheet = tech.page.getByRole("dialog");
    await sheet.getByRole("radiogroup", { name: "Modo de falha" }).waitFor({ timeout: 15_000 });
    const pos = sheet.locator("select").first();
    const posOpts = await pos.locator("option").allInnerTexts();
    if (posOpts.some((o) => /Eixo 1/.test(o))) await pos.selectOption({ label: "Eixo 1" });
    await sheet.getByRole("radiogroup", { name: "Modo de falha" }).getByRole("radio", { name: /BRD/ }).click().catch(async () => {
      await sheet.getByRole("button", { name: "Ver todos os modos" }).click();
      await sheet.getByRole("radiogroup", { name: "Modo de falha" }).getByRole("radio", { name: /BRD/ }).click();
    });
    await tech.page.screenshot({ path: `${OUT}/e7-phone-failure-form.png`, fullPage: true });
    await sheet.getByRole("button", { name: "Salvar dados da falha" }).click();
    await tech.page.getByTestId("exec-notice").filter({ hasText: /Dados da falha salvos/ }).waitFor({ timeout: 10_000 });
    const f = (await api("GET", `/api/failures/${wo.failure_id}`, planner)).data;
    check(f.failure_mode === "BRD", `failure mode BRD saved (position ${f.position_id ? "set" : "machine"})`, f.failure_mode);
    check(f.restored_at === null, "failure still open (restored_at is measured by the machine, not typed)");
    const overflow = await tech.page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    check(!overflow, "no horizontal overflow at 360 px");
    check(tech.errors.length === 0, "phone: no console errors", tech.errors);
    fs.writeFileSync(STATE, JSON.stringify({ woId, failureId: wo.failure_id }));
  } else {
    const { woId } = JSON.parse(fs.readFileSync(STATE, "utf8"));
    const tech = await newPage(browser, { width: 360, height: 740 }, true);
    await login(tech.page, "tecnico@factory-monitor.com", "Tecnico@1234");
    await tech.page.goto(`${BASE}/m/work-orders/${woId}`);
    await tech.page.getByRole("button", { name: /^Concluir$/ }).click();
    await tech.page.getByRole("dialog").getByRole("button", { name: "Concluir OS" }).click();
    await tech.page.getByTestId("exec-notice").filter({ hasText: /OS concluída/ }).waitFor({ timeout: 10_000 });
    await tech.page.screenshot({ path: `${OUT}/e7-phone-completed.png`, fullPage: true });
    const wo = (await api("GET", `/api/work-orders/${woId}`, planner)).data;
    check(wo.state === "completed", `${wo.number} completed on the phone (labor ${wo.labor_hours} h)`, wo.state);
    check(tech.errors.length === 0, "phone: no console errors", tech.errors);
  }
} finally {
  await browser.close();
}
console.log(failures ? `\n${failures} CHECK(S) FAILED` : "\nALL CHECKS PASSED");
process.exit(failures ? 1 : 0);
