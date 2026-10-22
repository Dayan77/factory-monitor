/**
 * QA S4 (re-verification round 3) — "Chamar manutenção" when NO maintenance person can be reached must not claim
 * that planning/management was told (fix: EmergencyCallResult.escalated_count).
 *
 * Against a dev-seeded live API (+ vite proxying to it). The script itself sets up the case and restores it:
 *   - technician on_call → false, planner deactivated (DELETE /api/users/{id}); the admin is the caller (excluded);
 *   - case A "only the supervisor": open manual UNK-01 stop on Robô de Solda (Linha de Montagem has a supervisor);
 *     the admin taps "Chamar manutenção" on /stops → API escalated_count 0, notified_count ≥ 1, warning
 *     "Nenhuma pessoa da manutenção…"; UI data-tone=danger, role=alert, text says radio/telefone and never
 *     "planejamento/gestão foi avisado";
 *   - case B "nobody at all": same on Torno CNC (Linha de Usinagem: no supervisor) through the API;
 *   - restore: planner re-activated (PATCH is_active) and technician on_call → true; the WOs are cancelled.
 *
 *   BASE_URL=http://localhost:5220 API=http://localhost:8120 node e2e/sprint4/emergency_nobody_reached.verify.mjs
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import fs from "node:fs";

const BASE = process.env.BASE_URL ?? "http://localhost:5220";
const API = process.env.API ?? "http://localhost:8120";
const OUT = process.env.OUT ?? "e2e/screenshots/sprint4";
fs.mkdirSync(OUT, { recursive: true });
let failures = 0;
const check = (c, m, x) => { if (c) console.log(`  ✓ ${m}`); else { failures++; console.log(`  ✗ ${m}${x !== undefined ? ` — got ${JSON.stringify(x)}` : ""}`); } };
async function api(method, p, token, body) {
  const r = await fetch(`${API}${p}`, { method, headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, data: await r.json().catch(() => null) };
}
const token = async (email, password) => (await api("POST", "/api/auth/login", null, { email, password })).data.access_token;
const list = (d) => (Array.isArray(d) ? d : d?.items ?? []);

const admin = await token("admin@factory-monitor.com", "Admin@1234");
const sup = await token("supervisor@factory-monitor.com", "Supervisor@1234");
const users = list((await api("GET", "/api/users/", admin)).data);
const planner = users.find((u) => u.email === "planejador@factory-monitor.com");
const tech = users.find((u) => u.email === "tecnico@factory-monitor.com");
const machines = (await api("GET", "/api/machines/", admin)).data;
const robot = machines.find((m) => m.name === "Robô de Solda");
const lathe = machines.find((m) => m.name === "Torno CNC");
const cancel = [];
const openStop = async (m) => (await api("POST", "/api/downtime/", admin, {
  machine_id: m.id, start: new Date(Date.now() - 4 * 60_000).toISOString(), reason_code: "UNK-01", comment: "QA nobody reached" })).data;

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
try {
  check((await api("PATCH", `/api/users/${tech.id}`, admin, { on_call: false })).status === 200, "setup: technician off call");
  check((await api("DELETE", `/api/users/${planner.id}`, admin)).status < 300, "setup: planner deactivated");
  const supBefore = new Set((await api("GET", "/api/notifications/", sup)).data.items.map((n) => n.id));
  const stopA = await openStop(robot);
  check(stopA?.id, "setup: open UNK-01 stop on Robô de Solda", stopA);

  // ── case A through the UI ──
  const context = await browser.newContext({ viewport: { width: 1024, height: 768 }, hasTouch: true, isMobile: true, timezoneId: "America/Manaus", locale: "pt-BR" });
  const page = await context.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  let callBody = null;
  page.on("response", async (r) => { if (r.url().includes("/api/work-orders/emergency-call")) callBody = await r.json().catch(() => null); });
  await page.goto(`${BASE}/login`);
  await page.getByLabel(/e-mail/i).fill("admin@factory-monitor.com");
  await page.getByLabel(/senha/i).fill("Admin@1234");
  await page.getByRole("button", { name: /entrar/i }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20_000 });
  await page.goto(`${BASE}/stops`);
  const btn = page.getByRole("region", { name: "Paradas sem justificativa" }).locator("li").filter({ hasText: "Robô de Solda" }).getByTestId("call-maintenance").first();
  await btn.waitFor({ timeout: 20_000 });
  await btn.click();
  const res = page.getByTestId("call-maintenance-result");
  await res.waitFor({ timeout: 15_000 });
  const msg = (await res.innerText()).replace(/\s+/g, " ");
  await page.screenshot({ path: `${OUT}/emergency-nobody-reached.png`, fullPage: true });
  console.log(`    result: "${msg}"`);
  const ec = callBody?.emergency_call ?? callBody?.work_order?.emergency_call ?? callBody;
  console.log(`    api: ${JSON.stringify(ec && { notified_count: ec.notified_count, on_call_technicians: ec.on_call_technicians, escalated_count: ec.escalated_count, fallback: ec.fallback, warning: ec.warning })}`);
  check(ec && ec.escalated_count === 0 && ec.on_call_technicians === 0 && ec.notified_count >= 1, "API: escalated_count 0, nobody on call, the supervisor counted in notified_count", ec);
  check(/Nenhuma pessoa da manutenção/.test(ec?.warning ?? ""), "API warning says no maintenance person was notified", ec?.warning);
  check((await res.getAttribute("data-tone")) === "danger", "UI tone danger", await res.getAttribute("data-tone"));
  check((await res.getAttribute("role")) === "alert", "UI announced as an alert");
  check(/nenhuma pessoa da manutenção/i.test(msg) && /rádio\/telefone/.test(msg), "UI tells the caller to warn maintenance by radio/phone");
  check(!/planejamento\/gestão foi avisado/.test(msg), "UI does NOT claim planning/management was told");
  const supNew = (await api("GET", "/api/notifications/", sup)).data.items.filter((n) => !supBefore.has(n.id) && n.kind === "emergency_call");
  check(supNew.length === 1, "the line supervisor got the emergency_call", supNew.length);
  check(errors.length === 0, "no console errors", errors);
  if (supNew[0]?.data?.work_order_id) cancel.push(supNew[0].data.work_order_id);
  else if (ec?.work_order_id) cancel.push(ec.work_order_id);

  // ── case B: nobody at all (Torno CNC, no supervisor on its line) through the API ──
  const stopB = await openStop(lathe);
  const rB = await api("POST", "/api/work-orders/emergency-call", admin, { downtime_event_id: stopB.id, machine_id: lathe.id });
  const ecB = rB.data?.emergency_call ?? rB.data;
  console.log(`    case B: ${rB.status} ${JSON.stringify(ecB && { notified_count: ecB.notified_count, escalated_count: ecB.escalated_count, warning: ecB.warning })}`);
  check(rB.status === 201 && ecB?.notified_count === 0 && ecB?.escalated_count === 0 && /ningu[eé]m|nenhuma/i.test(ecB?.warning ?? ""),
    "case B: notified 0, escalated 0, warning says nobody was reached", ecB);
  if (rB.data?.id) cancel.push(rB.data.id);
  for (const s of [stopA, stopB]) if (s?.id) await api("PATCH", `/api/downtime/${s.id}`, admin, { end: new Date().toISOString() });
} catch (e) {
  failures++; console.log(`  ✗ ${e}`);
} finally {
  for (const id of cancel) await api("POST", `/api/work-orders/${id}/transition`, admin, { to: "cancelled", cancel_reason: "duplicate" });
  const back = await api("PATCH", `/api/users/${planner.id}`, admin, { is_active: true });
  const oc = await api("PATCH", `/api/users/${tech.id}`, admin, { on_call: true });
  check(back.status === 200 && oc.status === 200, "restore: planner active again, technician on call again", [back.status, oc.status]);
  await browser.close();
}
console.log(failures ? `\n${failures} CHECK(S) FAILED` : "\nALL CHECKS PASSED");
process.exit(failures ? 1 : 0);
