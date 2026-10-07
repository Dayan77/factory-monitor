/**
 * Sprint 6 acceptance 5 (docs/sprint6.md §3, §6.5): operator tablet PWA on a tenant fed by the virtual plant
 * (STATE from onboarding_new_tenant.verify.mjs). Uses the plant's own unjustified stops (micro-stops / stops the
 * virtual plant produced), never fabricated ones.
 *
 *   - installable: Chrome's own check (CDP Page.getInstallabilityErrors) on the PRODUCTION build (PREVIEW_URL,
 *     `vite preview`) for /op, in a persistent profile; manifest id /op/; service worker controls the page
 *   - justify a stop in ≤ 3 taps (tablet 1280×800, dark + light)
 *   - offline: justify with the network cut → "sem conexão" banner + pending item; reconnect → arrives ONCE
 *     (server: one client_actions row for that action id, the stop justified with that reason)
 *   - the operator (line_ids = [Linha de Montagem]) gets 403 on another line's stop
 *
 *   STATE=… BASE_URL=http://localhost:5173 API=http://localhost:8000 PREVIEW_URL=http://localhost:5221 \
 *     node e2e/sprint6/operator_pwa.verify.mjs
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const BASE = process.env.BASE_URL ?? "http://localhost:5173";
const API = process.env.API ?? "http://localhost:8000";
const PREVIEW = process.env.PREVIEW_URL;
const REPO = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../..");
const OUT = process.env.OUT ?? path.join(REPO, "e2e/screenshots/sprint6");
const S = JSON.parse(fs.readFileSync(process.env.STATE, "utf8"));
fs.mkdirSync(OUT, { recursive: true });
let failures = 0;
const check = (c, m, got) => { console.log(`  ${c ? "✓" : "✗"} ${m}${c || got === undefined ? "" : `   got=${JSON.stringify(got).slice(0, 500)}`}`); if (!c) failures += 1; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function api(method, p, token, body, headers = {}) {
  const r = await fetch(`${API}${p}`, { method, headers: { "content-type": "application/json", ...headers, ...(token ? { authorization: `Bearer ${token}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, data: await r.json().catch(() => null), headers: r.headers };
}
const psql = (sql) => execFileSync("docker", ["exec", "fm_postgres", "psql", "-U", "fm_user", "-d", "factory_monitor", "-tAc", sql]).toString().trim();

const admin = (await api("POST", "/api/auth/login", null, { email: S.admin_email, password: S.admin_password })).data.access_token;
const tree = (await api("GET", "/api/plants/tree", admin)).data.find((p) => p.id === S.plant_id);
const montagem = tree.lines.find((l) => l.name === "Linha de Montagem");
const usinagem = tree.lines.find((l) => l.name === "Linha de Usinagem");
const opEmail = `qa-op-${Date.now().toString(36)}@example.com`; const opPw = `Qa!${crypto.randomBytes(9).toString("base64url")}`;
const u = await api("POST", "/api/users/", admin, { name: "Operador QA Montagem", email: opEmail, role: "operator", password: opPw, line_ids: [montagem.id] });
check(u.status === 201, "operator created with line_ids = [Linha de Montagem]", u);
const opTok = (await api("POST", "/api/auth/login", null, { email: opEmail, password: opPw })).data.access_token;
const opUserId = u.data.id;

async function unjustified(lineId) {
  const d = (await api("GET", "/api/downtime/?limit=500", admin)).data;
  return d.filter((x) => x.unjustified && x.line_id === lineId);
}
const VP = process.env.VP_CONTROL ?? "http://127.0.0.1:8173";
async function waitStops(lineId, n, ms) {
  // not enough natural stops yet → the virtual plant's control API stops a machine of the line for 20 s
  // (a real stop of the plant, read by the gateway like any other; it stays unjustified until someone justifies it)
  const t0 = Date.now();
  for (let k = 0; ; k++) {
    const l = await unjustified(lineId);
    if (l.length >= n || Date.now() - t0 > ms) return l;
    if (k % 6 === 0) await fetch(`${VP}/force-failure`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ machine: ["montagem.transportador", "montagem.robo"][k % 2], duration_s: 20 }) }).catch(() => {});
    await sleep(5000);
  }
}

// 403 on another line (API, operator token)
const other = (await api("GET", "/api/downtime/?limit=500", admin)).data.find((x) => x.line_id === usinagem.id);
if (other) {
  const r = await api("PATCH", `/api/downtime/${other.id}`, opTok, { reason_code: "MAT-01" });
  check(r.status === 403, "operator → other line's stop: 403", r);
  const l = await api("GET", `/api/downtime/?line_id=${usinagem.id}`, opTok);
  check(l.status === 403, "operator → other line's stop list: 403", l.status);
} else check(false, "an unjustified stop on Linha de Usinagem exists for the 403 check");

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
for (const scheme of ["dark", "light"]) {
  const stops = await waitStops(montagem.id, 2, 600000);
  if (stops.length < 2) { check(false, `[${scheme}] two unjustified stops on Linha de Montagem from the virtual plant`, stops.length); continue; }
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: "pt-BR", timezoneId: "America/Manaus", colorScheme: scheme, hasTouch: true });
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(`${BASE}/op/login`);
  await page.locator('input[autocomplete="username"]').fill(opEmail);
  await page.locator('input[type="password"]').fill(opPw);
  await page.locator('button[type="submit"]').click();
  await page.getByTestId("operator-app").waitFor({ timeout: 20000 });
  const lineBtn = page.getByRole("button", { name: "Linha de Montagem" });
  if (await lineBtn.isVisible().catch(() => false)) await lineBtn.click();
  await page.getByTestId("op-justify").waitFor({ timeout: 20000 });
  await page.screenshot({ path: path.join(OUT, `op_home_${scheme}.png`) });
  const box = await page.getByTestId("op-justify").boundingBox();
  const fs18 = await page.getByTestId("operator-app").evaluate((e) => getComputedStyle(e).fontSize);
  check(box.height >= 64 && fs18 === "18px", `[${scheme}] big targets (justify ${Math.round(box.width)}×${Math.round(box.height)} px, base font ${fs18})`);

  // online: 3 taps
  const target = stops[0];
  let taps = 0;
  await page.getByTestId("op-justify").tap(); taps++;
  await page.locator(`[data-testid=op-stop][href="/op/justify/${target.id}"]`).tap(); taps++;
  const sug = page.getByTestId("op-confirm-suggested");
  const reasonBtn = (await sug.isVisible().catch(() => false)) ? sug : page.getByTestId("op-reason").first();
  const reasonLabel = (await reasonBtn.innerText()).trim();
  await reasonBtn.tap(); taps++;
  check(taps <= 3, `[${scheme}] justified in ${taps} taps ("${reasonLabel.split("\n")[0]}")`);
  const t0 = Date.now(); let got;
  for (;;) { got = (await api("GET", "/api/downtime/?limit=500", admin)).data.find((x) => x.id === target.id); if (!got.unjustified || Date.now() - t0 > 20000) break; await sleep(500); }
  check(!got.unjustified && got.justified_by === opUserId, `[${scheme}] server: stop justified (${got.reason_code}) by the operator after the 5 s undo window (${Date.now() - t0} ms)`, got);
  await page.screenshot({ path: path.join(OUT, `op_after_justify_${scheme}.png`) });

  // offline
  const t2 = stops[1];
  // (i) probe: tablet (re)opened on the home screen, then the network drops → can a stop still be justified?
  await page.goto(`${BASE}/op`);
  await page.getByTestId("op-justify").waitFor();
  await ctx.setOffline(true);
  await page.getByTestId("op-justify").tap();
  await page.locator(`[data-testid=op-stop][href="/op/justify/${t2.id}"]`).tap();
  await sleep(4000);
  const reasonsAfterReload = await page.getByTestId("op-reason").count() + await page.getByTestId("op-confirm-suggested").count();
  const bodyAfterReload = (await page.locator("main, body").first().innerText()).replace(/\s+/g, " ").slice(-120);
  check(reasonsAfterReload > 0, `[${scheme}] offline right after (re)opening the app: reason buttons shown on the stop screen`, bodyAfterReload);
  await page.screenshot({ path: path.join(OUT, `op_offline_after_reload_${scheme}.png`) });
  await ctx.setOffline(false);
  // (ii) same SPA session that has loaded the reasons once online (in-app navigation, no reload)
  await page.getByTestId("op-reason").first().waitFor({ timeout: 30000 });           // reasons arrive once online
  await page.locator('a[href="/op/justify"]').first().tap();                         // "Voltar" → list (in-app)
  await page.locator(`[data-testid=op-stop][href="/op/justify/${t2.id}"]`).waitFor({ timeout: 15000 });
  await ctx.setOffline(true);
  await page.locator(`[data-testid=op-stop][href="/op/justify/${t2.id}"]`).tap();
  await sleep(1500);
  const rb = (await page.getByTestId("op-confirm-suggested").isVisible().catch(() => false)) ? page.getByTestId("op-confirm-suggested") : page.getByTestId("op-reason").first();
  await rb.tap().catch(async (e) => { await page.screenshot({ path: path.join(OUT, `op_offline_reason_missing_${scheme}.png`) }); console.log("  offline reason page text:", (await page.locator("body").innerText()).slice(0, 600)); throw e; });
  await page.getByTestId("op-offline").waitFor({ timeout: 15000 }).catch(() => {});
  await sleep(8000);
  const offlineBanner = await page.getByTestId("op-offline").isVisible().catch(() => false);
  const pending = await page.getByTestId("pending-count").innerText().catch(() => "");
  const stillOpen = (await api("GET", "/api/downtime/?limit=500", admin)).data.find((x) => x.id === t2.id);
  check(offlineBanner && /1/.test(pending) && stillOpen.unjustified, `[${scheme}] offline: banner "${(await page.getByTestId("op-offline").innerText().catch(() => "")).slice(0, 40)}", pending "${pending}", nothing reached the server`, { offlineBanner, pending, unjustified: stillOpen.unjustified });
  await page.screenshot({ path: path.join(OUT, `op_offline_${scheme}.png`) });
  await ctx.setOffline(false);
  const t3 = Date.now(); let arrived;
  for (;;) { arrived = (await api("GET", "/api/downtime/?limit=500", admin)).data.find((x) => x.id === t2.id); if (!arrived.unjustified || Date.now() - t3 > 60000) break; await sleep(500); }
  await sleep(5000);
  const rows = psql(`select count(*) || ' ' || coalesce(string_agg(action_id, ','), '') from client_actions where user_id='${opUserId}' and entity_id='${t2.id}'`);
  const [n] = rows.split(" ");
  const pendingAfter = await page.getByTestId("pending-count").isVisible().catch(() => false);
  check(!arrived.unjustified && n === "1" && !pendingAfter, `[${scheme}] reconnect: justification arrived after ${Date.now() - t3} ms, exactly once (client_actions rows: ${rows})`, { unjustified: arrived.unjustified, rows, pendingAfter });
  // replay of the same key (what a retry after a lost response does) → recorded result, nothing changes
  const key = rows.split(" ")[1];
  const replay = await api("PATCH", `/api/downtime/${t2.id}`, opTok, { reason_code: "MAT-01" }, { "X-Client-Action-Id": key });
  check(replay.status === 200 && replay.headers.get("idempotent-replay") === "true" && replay.data.reason_code === arrived.reason_code, `[${scheme}] same X-Client-Action-Id replayed → Idempotent-Replay, reason unchanged (${replay.data?.reason_code})`, replay.status);
  const relevant = errors.filter((e) => !/ERR_INTERNET_DISCONNECTED|Failed to fetch|net::|WebSocket|vibrate/.test(e));
  check(relevant.length === 0, `[${scheme}] no console errors (besides offline network errors)`, relevant.slice(0, 4));
  await ctx.close();
}

// installability on the production build
if (PREVIEW) {
  const prof = fs.mkdtempSync(path.join(os.tmpdir(), "qa-pwa-"));
  const pctx = await chromium.launchPersistentContext(prof, { executablePath: "/opt/pw-browsers/chromium", viewport: { width: 1280, height: 800 } });
  const page = pctx.pages()[0] ?? await pctx.newPage();
  await page.goto(`${PREVIEW}/op/login`);
  await page.waitForFunction(() => navigator.serviceWorker?.controller != null, null, { timeout: 20000 }).catch(() => {});
  await page.reload();
  await sleep(1500);
  const cdp = await pctx.newCDPSession(page);
  const inst = await cdp.send("Page.getInstallabilityErrors");
  const man = await cdp.send("Page.getAppManifest").catch(() => ({}));
  const id = JSON.parse(man.data ?? "{}").id;
  const ctrl = await page.evaluate(() => !!navigator.serviceWorker?.controller);
  check(inst.installabilityErrors.length === 0, `/op installable (Chrome installability errors: ${JSON.stringify(inst.installabilityErrors)})`, inst);
  check(/op\.webmanifest$/.test(man.url ?? "") && (id === "/op/" || id === undefined), `manifest ${man.url} id ${id}`, man.errors);
  check(ctrl, "service worker controls /op");
  await pctx.setOffline(true);
  await page.reload().catch(() => {});
  const shell = await page.getByTestId("op-login").isVisible().catch(() => false);
  check(shell, "app shell opens offline");
  await pctx.close();
} else console.log("  (PREVIEW_URL not set: installability not checked)");
await browser.close();
console.log(`\n${failures === 0 ? "OK" : "FAIL"} (${failures} failure(s))`);
process.exit(failures === 0 ? 0 : 1);
