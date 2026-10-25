/**
 * Sprint 7 gate (e) — brute-force lockout seen in the UI, against a REAL backend (no mocks).
 * On a throwaway invited user (deleted at the end): 5 wrong passwords on /login → the 6th attempt, even with the
 * RIGHT password, is refused with a visible lockout message (HTTP 429) and the user stays on /login.
 * An unknown e-mail gets the same "E-mail ou senha incorretos." as a wrong password (no enumeration).
 *
 *   BASE_URL=http://localhost:5173 API=http://localhost:8000 node e2e/sprint7b/lockout_ui.verify.mjs
 * Exit code 0 = every check passed.
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.BASE_URL ?? "http://localhost:5173";
const API = (process.env.API ?? "http://localhost:8000").replace(/\/$/, "");
const ADMIN = { email: process.env.E2E_EMAIL ?? "admin@factory-monitor.com", password: process.env.E2E_PASSWORD ?? "Admin@1234" };
const OUT = process.env.OUT ?? path.join(path.dirname(new URL(import.meta.url).pathname), "../screenshots/sprint7b");
fs.mkdirSync(OUT, { recursive: true });

let failures = 0;
const check = (cond, msg, got) => {
  if (cond) console.log(`  ✓ ${msg}`);
  else { failures += 1; console.log(`  ✗ ${msg}${got !== undefined ? ` — got: ${JSON.stringify(got).slice(0, 400)}` : ""}`); }
};
const post = async (p, body, tok) => fetch(`${API}${p}`, { method: "POST",
  headers: { "content-type": "application/json", ...(tok ? { authorization: `Bearer ${tok}` } : {}) }, body: JSON.stringify(body) });

const admin = await (await post("/api/auth/login", ADMIN)).json();
const email = `qa7b-lockui-${crypto.randomBytes(4).toString("hex")}@example.com`;
const inv = await post("/api/users/invitations", { email, name: "QA 7b lock UI", role: "operator" }, admin.access_token);
if (inv.status !== 201) { console.log("cannot invite", inv.status, await inv.text()); process.exit(1); }
const token = (await inv.json()).invite_url.split("token=")[1];
const pw = `Qg7b-${crypto.randomBytes(9).toString("base64url")}!9`;
const acc = await post("/api/auth/invitations/accept", { token, password: pw });
if (acc.status !== 201) { console.log("cannot accept", acc.status, await acc.text()); process.exit(1); }
const userTok = (await acc.json()).access_token;

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const page = await browser.newPage();
const consoleErrors = [];
page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
const statuses = [];
page.on("response", (r) => { if (r.url().endsWith("/api/auth/login")) statuses.push(r.status()); });

const attempt = async (e, p) => {
  await page.fill("#login-email", e);
  await page.fill("#login-password", p);
  const resp = page.waitForResponse((r) => r.url().endsWith("/api/auth/login"));
  await page.click("button[type=submit]");
  await resp;
  await page.waitForTimeout(250);
  return (await page.locator("[role=alert], [role=status]").allInnerTexts()).join(" | ");
};

console.log("brute-force lockout (UI)");
await page.goto(`${BASE}/login`);
let msg = "";
for (let i = 0; i < 5; i += 1) msg = await attempt(email, `wrong-${i}`);
check(statuses.slice(0, 4).every((s) => s === 401), "first wrong passwords → 401", statuses);
msg = await attempt(email, pw);
const last = statuses[statuses.length - 1];
check(last === 429, "right password while locked → 429", statuses);
console.log(`    message: ${msg}`);
check(/tentativas|bloquead|aguarde|minutos/i.test(msg) && !/E-mail ou senha incorretos/.test(msg),
  "lockout message is visible and differs from the wrong-password one", msg);
check(new URL(page.url()).pathname === "/login", "still on /login (not signed in)", page.url());
await page.screenshot({ path: path.join(OUT, "lockout-login.png"), fullPage: true });

await page.goto(`${BASE}/login`);
const ghost = await attempt(`ghost-${crypto.randomBytes(3).toString("hex")}@example.com`, "whatever-1");
check(/E-mail ou senha incorretos/.test(ghost), "unknown e-mail → same 'E-mail ou senha incorretos.'", ghost);
const unexpected = consoleErrors.filter((t) => !/401|429|Unauthorized|Too Many/i.test(t));
check(unexpected.length === 0, "no console errors besides the expected 401/429", unexpected);
await browser.close();

const del = await fetch(`${API}/api/account`, { method: "DELETE",
  headers: { "content-type": "application/json", authorization: `Bearer ${userTok}` }, body: JSON.stringify({ password: pw, confirm: "EXCLUIR" }) });
console.log(`  (cleanup: throwaway user deleted → ${del.status})`);

console.log(failures ? `\n${failures} check(s) FAILED` : "\nall checks passed");
process.exit(failures ? 1 : 0);
