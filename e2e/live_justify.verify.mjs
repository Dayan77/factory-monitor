/**
 * Live run (accelerated virtual plant → gateway → server): the operator justifies a real, automatically detected
 * stop on the tablet UI and the machine's Pareto/OEE change accordingly (API before/after).
 *   BASE_URL=http://localhost:5202 API=http://localhost:8176 MACHINE="Torno CNC" START=<iso> node e2e/live_justify.verify.mjs
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
const BASE = process.env.BASE_URL ?? "http://localhost:5202";
const API = process.env.API ?? "http://localhost:8176";
const NAME = process.env.MACHINE ?? "Torno CNC";
const USER = { email: process.env.E2E_EMAIL ?? "admin@factory-monitor.com", password: process.env.E2E_PASSWORD ?? "Admin@1234" };
let failures = 0;
const check = (c, m) => { console.log(`  ${c ? "✓" : "✗"} ${m}`); if (!c) failures += 1; };
const tok = (await (await fetch(`${API}/api/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(USER) })).json()).access_token;
const get = async (p, q = {}) => { const u = new URL(API + p); for (const [k, v] of Object.entries(q)) u.searchParams.set(k, v); return (await fetch(u, { headers: { Authorization: `Bearer ${tok}` } })).json(); };
const m = (await get("/api/machines/")).find((x) => x.name === NAME);
const win = { start: process.env.START, stop: new Date().toISOString() };
const evs = (await get("/api/downtime/", { machine_id: m.id, unjustified: "true", ...win })).filter((e) => e.end);
check(evs.length > 0, `${NAME}: ${evs.length} closed unjustified automatic stop(s)`);
const ev = evs.sort((a, b) => a.start.localeCompare(b.start))[0];
const before = await get("/api/losses/pareto", { scope: "machine", id: m.id, ...win });
const oBefore = await get("/api/oee", { scope: "machine", id: m.id, ...win });
const row = (rows, code) => rows.find((r) => r.reason_code === code);
console.log(`    target stop ${ev.start} ${ev.duration_s} s (${ev.reason_code ?? "sem motivo"}, sugerido ${ev.suggested_reason_code ?? "—"})`);

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const ctx = await browser.newContext({ viewport: { width: 1024, height: 768 }, hasTouch: true, timezoneId: "America/Manaus" });
const page = await ctx.newPage();
const errors = [];
page.on("console", (x) => { if (x.type() === "error") errors.push(x.text()); });
page.on("pageerror", (e) => errors.push(String(e)));
await page.goto(`${BASE}/login`);
await page.getByLabel(/e-mail/i).fill(USER.email);
await page.getByLabel(/senha/i).fill(USER.password);
await page.getByRole("button", { name: /entrar/i }).click();
await page.waitForURL(/dashboard/);
await page.goto(`${BASE}/stops`);
await page.getByTestId("stop-card").first().waitFor({ timeout: 20_000 });
const hhmm = new Date(ev.start).toLocaleTimeString("pt-BR", { timeZone: "America/Manaus", hour: "2-digit", minute: "2-digit" });
const card = page.getByTestId("stop-card").filter({ hasText: NAME }).filter({ hasText: hhmm });
check((await card.count()) >= 1, `card for ${NAME} ${hhmm} listed`);
await card.first().click();
const dialog = page.getByRole("dialog", { name: "Justificar parada" });
await dialog.waitFor();
await dialog.getByRole("radio", { name: /Falta de material/ }).first().click();
const [resp] = await Promise.all([
  page.waitForResponse((r) => r.url().includes(`/api/downtime/${ev.id}`) && r.request().method() === "PATCH"),
  dialog.getByRole("button", { name: "Confirmar motivo" }).click(),
]);
check(resp.status() === 200, `PATCH /api/downtime/${ev.id.slice(0, 8)}… → ${resp.status()}`);
await page.waitForTimeout(1500);
check(errors.length === 0, `zero console errors${errors.length ? ": " + errors.join(" | ") : ""}`);
await browser.close();

const after = await get("/api/losses/pareto", { scope: "machine", id: m.id, ...win });
const oAfter = await get("/api/oee", { scope: "machine", id: m.id, ...win });
const prevCode = ev.effective_reason_code ?? "UNK-01";
const mat0 = row(before, "MAT-01")?.duration_s ?? 0, mat1 = row(after, "MAT-01")?.duration_s ?? 0;
const p0 = row(before, prevCode)?.duration_s ?? 0, p1 = row(after, prevCode)?.duration_s ?? 0;
console.log(`    Pareto ${prevCode}: ${p0} → ${p1} s; MAT-01: ${mat0} → ${mat1} s; OEE ${oBefore.oee} → ${oAfter.oee}`);
check(Math.abs(mat1 - mat0 - ev.duration_s) < 1, "Pareto: MAT-01 grew by the stop's duration");
check(Math.abs(p0 - p1 - ev.duration_s) < 1, `Pareto: ${prevCode} shrank by the same amount`);
check(Math.abs(oAfter.availability - oBefore.availability) < 0.01, "availability unchanged (unplanned → unplanned reason)");
console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
