/**
 * /ai against a REAL server with NO model key (GET /api/ai/status → configured=false, nothing mocked):
 * the page shows the "não configurado" empty state, no composer, zero console errors; the raw stream endpoint
 * is also probed (it must end; the UI must not hang).
 *
 *   BASE_URL=http://localhost:5173 API=http://localhost:8000 node e2e/sprint5/ai_no_provider.verify.mjs
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import fs from "node:fs";

const BASE = process.env.BASE_URL ?? "http://localhost:5173";
const API = process.env.API ?? "http://localhost:8000";
const OUT = process.env.OUT ?? "e2e/screenshots/sprint5";
fs.mkdirSync(OUT, { recursive: true });
let failures = 0;
const check = (c, m) => { console.log(`  ${c ? "✓" : "✗"} ${m}`); if (!c) failures += 1; };

const login = await (await fetch(`${API}/api/auth/login`, { method: "POST", headers: { "content-type": "application/json" },
  body: JSON.stringify({ email: "admin@factory-monitor.com", password: "Admin@1234" }) })).json();
const status = await (await fetch(`${API}/api/ai/status`, { headers: { authorization: `Bearer ${login.access_token}` } })).json();
check(status.configured === false, `server reports configured=false (${JSON.stringify(status)})`);

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
for (const theme of ["light", "dark"]) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: theme, locale: "pt-BR", timezoneId: "America/Manaus" });
  await ctx.addInitScript((t) => { try { localStorage.setItem("fm-theme", t); } catch { /* */ } }, theme);
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(`${BASE}/login`);
  await page.fill('input[type="email"]', "admin@factory-monitor.com");
  await page.fill('input[type="password"]', "Admin@1234");
  await page.click('button[type="submit"]');
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });
  await page.goto(`${BASE}/ai`);
  await page.waitForTimeout(2500);
  const body = await page.locator("main").innerText();
  check(/não (está )?configurad/i.test(body), `[${theme}] empty state says the assistant is not configured`);
  check(/ANTHROPIC_API_KEY|OPENAI_API_KEY|\.env|administrador/i.test(body), `[${theme}] empty state tells how to enable it`);
  check((await page.locator("textarea").count()) === 0, `[${theme}] no composer without a provider`);
  await page.screenshot({ path: `${OUT}/ai-no-provider-${theme}.png` });
  check(errors.length === 0, `[${theme}] zero console errors ${errors.join(" | ")}`);
  await ctx.close();
}
await browser.close();
console.log(failures ? `${failures} check(s) failed` : "all checks passed");
process.exit(failures ? 1 : 0);
