/**
 * Sprint 4 §7 item 2 (UI half of the history): after scenario_e_ui.verify.mjs swapped the POS-A1 component, the
 * machine page → position drawer "Histórico de componentes" shows the removed component with "Idade na remoção"
 * (cycles · hours) and the new one with "Idade: 0 ciclos". Same setup/env as scenario_e_ui.verify.mjs.
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import fs from "node:fs";
const BASE = process.env.BASE_URL ?? "http://localhost:5210";
const API = process.env.API ?? "http://localhost:8110";
const OUT = process.env.OUT ?? "e2e/screenshots/sprint4";
const PW = process.env.PASSWORD ?? "Test@12345";
fs.mkdirSync(OUT, { recursive: true });
let failures = 0;
const check = (c, m, x) => { if (c) console.log(`  ✓ ${m}`); else { failures++; console.log(`  ✗ ${m}${x !== undefined ? ` — got ${JSON.stringify(x)}` : ""}`); } };
const login = async (email) => (await (await fetch(`${API}/api/auth/login`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email, password: PW }) })).json()).access_token;
const tok = await login(process.env.PLANNER);
const status = await (await fetch(`${API}/api/maintenance-plans/status`, { headers: { authorization: `Bearer ${tok}` } })).json();
const pl1 = status.find((r) => /Cilindro A1/.test(r.plan_name));
const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 }, timezoneId: "America/Manaus", locale: "pt-BR" });
await ctx.clock.setFixedTime(new Date("2026-12-01T12:00:00Z"));
const page = await ctx.newPage();
const errors = [];
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
page.on("pageerror", (e) => errors.push(String(e)));
await page.goto(`${BASE}/login`);
await page.getByLabel(/e-mail/i).fill(process.env.ADMIN);
await page.getByLabel(/senha/i).fill(PW);
await page.getByRole("button", { name: /entrar/i }).click();
await page.waitForURL((u) => !u.pathname.startsWith("/login"));
await page.goto(`${BASE}/factory/machines/${pl1.machine_id}`);
const row = page.getByTestId("position-row").filter({ hasText: "Cilindro A1" }).first();
await row.waitFor({ timeout: 20000 });
await row.click();
const hist = page.locator("section,div").filter({ hasText: "Histórico de componentes" }).last();
await page.getByText("Histórico de componentes").waitFor({ timeout: 15000 });
await page.waitForTimeout(1500);
const text = (await page.locator('[role="dialog"], aside').last().innerText()).replace(/\s+/g, " ");
await page.screenshot({ path: `${OUT}/position_history.png` });
const ov = await page.evaluate(() => { const d = document.querySelector('[role="dialog"]') ?? document.querySelector("aside:last-of-type"); const r = d?.getBoundingClientRect(); return { right: r?.right, vw: innerWidth, sw: d?.scrollWidth, cw: d?.clientWidth, docSW: document.documentElement.scrollWidth }; });
check(ov.right <= ov.vw + 1 && ov.docSW <= ov.vw + 1, "drawer fits the viewport (no horizontal overflow)", ov);
console.log("drawer:", text.slice(text.indexOf("Histórico"), text.indexOf("Histórico") + 400));
check(/Idade na remoção: 340\.000 ciclos · 300,0 h/.test(text), "removed component shows 'Idade na remoção: 340.000 ciclos · 300,0 h'");
check(/QA-A1-\d+[^]*?Idade: 0 ciclos · 0,0 h/.test(text), "new component shows 'Idade: 0 ciclos · 0,0 h'");
check(errors.length === 0, "no console errors", errors);
await browser.close();
console.log(failures ? `${failures} FAILED` : "ALL CHECKS PASSED");
process.exit(failures ? 1 : 0);
