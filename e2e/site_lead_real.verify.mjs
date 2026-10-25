// QA: the public site's contact form against the REAL leads API. The built site posts to
// https://app.factoryconnect.tech/api/public/leads (unreachable here); Playwright forwards that exact request
// (method, headers incl. Origin, body) to a running server (API, default http://localhost:8000) and returns the
// real response, CORS headers included, so the browser enforces the real CORS policy.
//   cd site && npx astro build && node ../e2e/site_lead_real.verify.mjs
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
const here = path.dirname(fileURLToPath(import.meta.url));
const PW = existsSync("/opt/node-tools/node_modules/playwright/index.mjs") ? "/opt/node-tools/node_modules/playwright/index.mjs" : "playwright";
const { chromium } = await import(PW);
const { serve } = await import(path.join(here, "../site/tests/serve.mjs"));
const API = process.env.API ?? "http://localhost:8000";
const PORT = Number(process.env.E2E_PORT || 5264);
const server = await serve(path.join(here, "../site/dist"), PORT);
const browser = await chromium.launch({ executablePath: existsSync("/opt/pw-browsers/chromium") ? "/opt/pw-browsers/chromium" : undefined });
const page = await browser.newPage();
const errors = []; page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
let seen = null, status = null;
const ip = `10.77.${Math.floor(Math.random() * 250)}.${Math.floor(Math.random() * 250)}`;
await page.route("https://app.factoryconnect.tech/api/public/leads", async (route) => {
  const req = route.request();
  const headers = { ...req.headers(), "x-forwarded-for": ip, origin: "https://factoryconnect.tech" };
  const res = await fetch(API + "/api/public/leads", { method: req.method(), headers, body: req.postData() ?? undefined });
  if (req.method() === "POST") { seen = JSON.parse(req.postData()); status = res.status; }
  const h = Object.fromEntries(res.headers.entries());
  // the page origin is http://127.0.0.1:PORT; map the API's allow-origin for the real site origin onto it
  if (h["access-control-allow-origin"] === "https://factoryconnect.tech") h["access-control-allow-origin"] = `http://127.0.0.1:${PORT}`;
  await route.fulfill({ status: res.status, headers: h, body: await res.text() });
});
await page.goto(`http://127.0.0.1:${PORT}/contato`, { waitUntil: "networkidle" });
const email = `qa-site-${Date.now()}@qa-factoryconnect.com.br`;
await page.fill("#f-name", "QA Site"); await page.fill("#f-email", email);
await page.fill("#f-company", "QA"); await page.fill("#f-message", "Lead real do site (QA)");
await page.check("#f-consent");
await page.click("[data-submit]");
await page.locator("#form-success").waitFor({ state: "visible", timeout: 15000 });
const ok = status === 202 && seen?.consent === true && seen?.website === "" && seen?.email === email && errors.length === 0;
console.log(`status=${status} body=${JSON.stringify(seen)} errors=${JSON.stringify(errors)}`);
console.log(ok ? "PASS site form -> real API 202 and success state" : "FAIL");
await browser.close(); server.close?.();
console.log(`EMAIL=${email}`);
process.exit(ok ? 0 : 1);
