/**
 * QA S4 — "Chamar manutenção" on a line with NO on-call technician (dev seed: Linha de Usinagem / Torno CNC)
 * must not report a plain success: the call escalates to planning/management and the UI shows a warning.
 * Needs an open, unjustified stop on Torno CNC (e.g. a fault on the virtual plant, or an auto_fault downtime row).
 *
 *   BASE_URL=http://localhost:5221 API=http://localhost:8121 node e2e/sprint4/emergency_no_oncall.verify.mjs
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import fs from "node:fs";

const BASE = process.env.BASE_URL ?? "http://localhost:5220";
const API = process.env.API ?? "http://localhost:8120";
const OUT = process.env.OUT ?? "e2e/screenshots/sprint4";
const MACHINE = process.env.MACHINE ?? "Torno CNC";
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

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const planner = await token("planejador@factory-monitor.com", "Planner@1234");
try {
  const before = new Set((await api("GET", "/api/notifications/", planner)).data.items.map((n) => n.id));
  const context = await browser.newContext({ viewport: { width: 1024, height: 768 }, hasTouch: true, isMobile: true, timezoneId: "America/Manaus", locale: "pt-BR" });
  const page = await context.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(`${BASE}/login`);
  await page.getByLabel(/e-mail/i).fill("admin@factory-monitor.com");
  await page.getByLabel(/senha/i).fill("Admin@1234");
  await page.getByRole("button", { name: /entrar/i }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20_000 });
  await page.goto(`${BASE}/stops`);
  const btn = page.getByRole("region", { name: "Paradas sem justificativa" }).locator("li").filter({ hasText: MACHINE }).getByTestId("call-maintenance").first();
  await btn.waitFor({ timeout: 20_000 });
  await btn.click();
  const res = page.getByTestId("call-maintenance-result");
  await res.waitFor({ timeout: 15_000 });
  const msg = await res.innerText();
  await page.screenshot({ path: `${OUT}/emergency-no-oncall.png`, fullPage: true });
  console.log(`    result: "${msg}"`);
  check((await res.getAttribute("data-tone")) !== "ok", "not shown as a plain success", await res.getAttribute("data-tone"));
  check(/plantão/.test(msg) && !/Manutenção chamada/.test(msg), "tells the caller nobody is on call on this line");
  check((await res.getAttribute("role")) === "alert", "announced as an alert");
  const fresh = (await api("GET", "/api/notifications/", planner)).data.items.filter((n) => !before.has(n.id) && n.kind === "emergency_call");
  check(fresh.length === 1 && fresh[0].data.no_on_call === true, "planner received the escalated emergency call", fresh.length);
  check(errors.length === 0, "no console errors", errors);
  if (fresh[0]?.data?.work_order_id) {
    await api("POST", `/api/work-orders/${fresh[0].data.work_order_id}/transition`, planner, { to: "cancelled", cancel_reason: "duplicate" });
  }
} finally {
  await browser.close();
}
console.log(failures ? `\n${failures} CHECK(S) FAILED` : "\nALL CHECKS PASSED");
process.exit(failures ? 1 : 0);
