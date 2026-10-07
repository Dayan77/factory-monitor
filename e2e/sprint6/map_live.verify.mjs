/**
 * Sprint 6 acceptance 1 + 3 (docs/sprint6.md §1, §6.1, §6.3) on a tenant fed by the virtual plant through a real
 * gateway (the one onboarding_new_tenant.verify.mjs leaves running; STATE = its state file).
 *
 *   §6.3 layout rules over HTTP: duplicate marker → 409; machine of another plant → 422; layout of another tenant →
 *        404; SVG with <script> → 422; coordinates outside [0, 1] → 422.
 *   §6.1 a floor plan (4000×2500, s6-layout-example) is published with every machine of the plant placed; the
 *        virtual plant's control API forces a failure / repairs it and Playwright measures how long the marker takes
 *        to show `failure` / leave it (≤ 2 s); then the WebSocket is dropped (routeWebSocket: closed + reconnects
 *        refused) and "ao vivo pausado" must appear in ≤ 30 s.
 *
 *   STATE=/tmp/onb_state.json BASE_URL=http://localhost:5173 API=http://localhost:8000 VP_CONTROL=http://127.0.0.1:8173 \
 *     node e2e/sprint6/map_live.verify.mjs
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.BASE_URL ?? "http://localhost:5173";
const API = process.env.API ?? "http://localhost:8000";
const VP = process.env.VP_CONTROL ?? "http://127.0.0.1:8173";
const REPO = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../..");
const OUT = process.env.OUT ?? path.join(REPO, "e2e/screenshots/sprint6");
const S = JSON.parse(fs.readFileSync(process.env.STATE, "utf8"));
fs.mkdirSync(OUT, { recursive: true });
let failures = 0;
const check = (c, m, got) => { console.log(`  ${c ? "✓" : "✗"} ${m}${c || got === undefined ? "" : `   got=${JSON.stringify(got).slice(0, 400)}`}`); if (!c) failures += 1; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function api(method, p, token, body, raw) {
  const r = await fetch(`${API}${p}`, { method, headers: { ...(raw ? {} : { "content-type": "application/json" }), ...(token ? { authorization: `Bearer ${token}` } : {}) }, body: raw ?? (body ? JSON.stringify(body) : undefined) });
  return { status: r.status, data: await r.json().catch(() => null) };
}
const upload = (id, tok, name, type, content) => { const fd = new FormData(); fd.append("file", new Blob([content], { type }), name); return api("POST", `/api/layouts/${id}/image`, tok, null, fd); };
const vp = async (p, body) => { const r = await fetch(`${VP}${p}`, { method: body ? "POST" : "GET", headers: { "content-type": "application/json" }, body: body ? JSON.stringify(body) : undefined }); return { status: r.status, data: await r.json().catch(() => null) }; };

const tok = (await api("POST", "/api/auth/login", null, { email: S.admin_email, password: S.admin_password })).data.access_token;
const other = (await api("POST", "/api/auth/login", null, { email: "admin@factory-monitor.com", password: "Admin@1234" })).data.access_token;
const tree = (await api("GET", "/api/plants/tree", tok)).data;
const plant = tree.find((p) => p.id === S.plant_id);
const machines = plant.lines.flatMap((l) => l.machines.map((m) => ({ ...m, line_id: l.id })));
const byName = Object.fromEntries(machines.map((m) => [m.name, m]));

// ── layout rules (§6.3) ─────────────────────────────────────────────────────
const spec = fs.readFileSync(path.join(REPO, "docs/sprint6.md"), "utf8");
const ex = JSON.parse(spec.match(/```json s6-layout-example\n([\s\S]*?)\n```/)[1]);
const svg = (extra = "") => `<svg xmlns="http://www.w3.org/2000/svg" width="${ex.image.width_px}" height="${ex.image.height_px}" viewBox="0 0 ${ex.image.width_px} ${ex.image.height_px}">` +
  `<rect width="100%" height="100%" fill="#f1f5f9"/><rect x="200" y="150" width="1600" height="1300" fill="none" stroke="#334155" stroke-width="12"/>` +
  `<rect x="1900" y="150" width="1900" height="1300" fill="none" stroke="#334155" stroke-width="12"/><rect x="3300" y="1900" width="600" height="500" fill="#e2e8f0" stroke="#334155" stroke-width="8"/>${extra}</svg>`;
let existing = (await api("GET", `/api/plants/${plant.id}/layouts`, tok)).data ?? [];
let draft = existing.find((l) => l.status === "draft");
if (!draft) { const r = await api("POST", `/api/plants/${plant.id}/layouts`, tok, {}); check(r.status === 201, "create draft layout", r); draft = r.data; }
const second = await api("POST", `/api/plants/${plant.id}/layouts`, tok, {});
check(second.status === 409, "second draft → 409", second.status);
const bad = await upload(draft.id, tok, "plan.svg", "image/svg+xml", svg('<script>alert(1)</script>'));
check(bad.status === 422, "SVG with <script> rejected (422)", bad);
const onload = await upload(draft.id, tok, "plan.svg", "image/svg+xml", svg('<rect onload="alert(1)" width="1" height="1"/>'));
check(onload.status === 422, "SVG with on* attribute rejected (422)", onload.status);
const ok = await upload(draft.id, tok, "plan.svg", "image/svg+xml", svg());
check(ok.status === 200 && ok.data.image_width_px === 4000 && ok.data.image_height_px === 2500, "clean 4000×2500 SVG accepted", ok.data);
const markers = ex.markers.map((m) => m.kind === "machine" ? { kind: "machine", machine_id: byName[m.machine].id, x: m.x, y: m.y, rotation_deg: m.rotation_deg, size: m.size }
  : m.kind === "line_label" ? { kind: "line_label", line_id: plant.lines.find((l) => l.name === m.line).id, x: m.x, y: m.y } : { kind: "area_label", label: m.label, x: m.x, y: m.y });
const placed = new Set(ex.markers.filter((m) => m.kind === "machine").map((m) => m.machine));
const extra = machines.filter((m) => !placed.has(m.name));
extra.forEach((m, i) => markers.push({ kind: "machine", machine_id: m.id, x: 0.2 + 0.1 * i, y: 0.7, rotation_deg: 0, size: "m" }));
const machineMarkers = markers.filter((m) => m.kind === "machine").length;
const dup = await api("PUT", `/api/layouts/${draft.id}/markers`, tok, { markers: [...markers, { ...markers[1], x: 0.5 }] });
check(dup.status === 409, "duplicate machine marker → 409", dup);
const oob = await api("PUT", `/api/layouts/${draft.id}/markers`, tok, { markers: [{ ...markers[1], x: 1.2 }] });
check(oob.status === 422, "x = 1.2 (outside [0, 1]) → 422", oob.status);
const oob2 = await api("PUT", `/api/layouts/${draft.id}/markers`, tok, { markers: [{ ...markers[1], y: -0.01 }] });
check(oob2.status === 422, "y = −0.01 → 422", oob2.status);
// machine of another plant of the SAME tenant
let p2 = tree.find((p) => p.name === "Planta QA 2");
if (!p2) {
  const np = (await api("POST", "/api/plants/", tok, { name: "Planta QA 2", timezone: "America/Manaus" })).data;
  const nl = (await api("POST", "/api/lines/", tok, { name: "Linha QA 2", plant_id: np.id })).data;
  await api("POST", "/api/machines/", tok, { name: "Máquina QA 2", line_id: nl.id });
  p2 = (await api("GET", "/api/plants/tree", tok)).data.find((p) => p.id === np.id);
}
const m2 = p2.lines[0]?.machines[0];
const wrongPlant = await api("PUT", `/api/layouts/${draft.id}/markers`, tok, { markers: [{ kind: "machine", machine_id: m2?.id, x: 0.5, y: 0.5 }] });
check(!!m2 && wrongPlant.status === 422, "machine of another plant → 422", wrongPlant);
// another tenant
for (const [m, p, b] of [["GET", `/api/layouts/${draft.id}`], ["PUT", `/api/layouts/${draft.id}/markers`, { markers: [] }], ["POST", `/api/layouts/${draft.id}/publish`],
  ["GET", `/api/layouts/${draft.id}/image`], ["GET", `/api/plants/${plant.id}/layout/published`], ["GET", `/api/plants/${plant.id}/layout/auto`], ["POST", `/api/plants/${plant.id}/layouts`, {}]]) {
  const r = await api(m, p, other, b);
  check(r.status === 404, `other tenant: ${m} ${p.replace(draft.id, "{layout}").replace(plant.id, "{plant}")} → 404`, r.status);
}
const imgNoTok = await fetch(`${API}/api/layouts/${draft.id}/image`);
check(imgNoTok.status === 401, "layout image without token → 401", imgNoTok.status);
const put = await api("PUT", `/api/layouts/${draft.id}/markers`, tok, { markers });
check(put.status === 200, `markers saved (${machineMarkers} machines + labels)`, put);
const pub = await api("POST", `/api/layouts/${draft.id}/publish`, tok);
check(pub.status === 200 && pub.data.status === "published", "layout published", pub.status);
const pubView = (await api("GET", `/api/plants/${plant.id}/layout/published`, tok)).data;
check(pubView.unplaced_machine_ids.length === 0 && pubView.machines.length === machines.length, `published view: ${machines.length} machines, none unplaced`, pubView.unplaced_machine_ids);

// ── live map (§6.1) ─────────────────────────────────────────────────────────
const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const ctx = await browser.newContext({ viewport: { width: 1366, height: 768 }, locale: "pt-BR", timezoneId: "America/Manaus" });
let wsMode = "pass";
const wsServers = [];
await ctx.routeWebSocket(/\/ws\//, (ws) => {
  if (wsMode === "drop") { ws.close({ code: 1011, reason: "qa drop" }); return; }
  const server = ws.connectToServer();
  wsServers.push({ ws, server });
});
const page = await ctx.newPage();
const errors = [];
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
page.on("pageerror", (e) => errors.push(String(e)));
await page.goto(`${BASE}/login`);
await page.fill('input[type="email"]', S.admin_email);
await page.fill('input[type="password"]', S.admin_password);
await page.click('button[type="submit"]');
await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });
await page.goto(`${BASE}/factory?plant=${plant.id}`);
await page.getByTestId("map-marker").first().waitFor({ timeout: 20000 });
await sleep(1500);
const nMarkers = await page.getByTestId("map-marker").count();
check(nMarkers === machineMarkers, `map shows ${machineMarkers} machine markers on the floor plan`, nMarkers);
check(await page.locator("[data-testid=map-inner] img, [data-testid=map-inner] image").count() > 0, "floor-plan image rendered");
await page.screenshot({ path: path.join(OUT, "map_published.png") });
const stateOf = (name) => page.locator(`[data-testid=map-marker][aria-label^="${name}:"]`).getAttribute("data-state");
const waitState = async (name, pred, ms) => { const t0 = Date.now(); for (;;) { const s = await stateOf(name); if (pred(s)) return { s, dt: Date.now() - t0 }; if (Date.now() - t0 > ms) return { s, dt: null }; await sleep(25); } };
console.log("  initial states:", JSON.stringify(Object.fromEntries(await Promise.all(machines.map(async (m) => [m.name, await stateOf(m.name)])))));

const results = [];
const vpStatus = (await vp("/status")).data.machines;
const keyOf = Object.fromEntries(Object.entries(vpStatus).map(([k, v]) => [v.name, k]));
for (const name of ["Robô de Solda", "Torno CNC", "Transportador de Saída"]) {
  const before = await stateOf(name);
  if (before === "offline" || before === "off_shift" || before === "planned_stop") { console.log(`  (skip ${name}: ${before})`); continue; }
  const t0 = Date.now();
  const f = await vp("/force-failure", { machine: keyOf[name] });
  if (f.status !== 200) { check(false, `force-failure ${name}`, f); continue; }
  const r1 = await waitState(name, (s) => s === "failure", 10000);
  const dtF = r1.dt === null ? null : Date.now() - t0 - (Date.now() - t0 - r1.dt) ;
  results.push({ name, transition: `${before}→failure`, ms: r1.dt });
  check(r1.dt !== null && r1.dt <= 2000, `${name}: virtual plant failure → marker "failure" in ${r1.dt} ms (≤ 2000)`, r1);
  if (name === "Robô de Solda") await page.screenshot({ path: path.join(OUT, "map_failure.png") });
  await sleep(3000);
  const t1 = Date.now();
  const rp = await vp("/repair", { machine: keyOf[name] });
  const r2 = await waitState(name, (s) => s !== "failure", 10000);
  results.push({ name, transition: `failure→${r2.s}`, ms: r2.dt });
  check(rp.status === 200 && r2.dt !== null && r2.dt <= 2000, `${name}: repair → marker leaves "failure" (${r2.s}) in ${r2.dt} ms (≤ 2000)`, { rp: rp.status, r2, since: Date.now() - t1 });
  void dtF;
  await sleep(2000);
}
console.log("  latencies:", JSON.stringify(results));

// drop the WebSocket
wsMode = "drop";
const tDrop = Date.now();
for (const { ws } of wsServers) ws.close({ code: 1011, reason: "qa drop" });
await page.getByTestId("live-paused").waitFor({ timeout: 40000 }).catch(() => {});
const dtPause = Date.now() - tDrop;
const pausedVisible = await page.getByTestId("live-paused").isVisible();
check(pausedVisible && dtPause <= 30000, `"ao vivo pausado" after WS drop in ${(dtPause / 1000).toFixed(1)} s (≤ 30 s)`, { pausedVisible, dtPause });
await page.screenshot({ path: path.join(OUT, "map_live_paused.png") });
wsMode = "pass";
await page.getByTestId("live-paused").waitFor({ state: "hidden", timeout: 45000 }).catch(() => {});
check(!(await page.getByTestId("live-paused").isVisible()), "banner goes away after reconnect");

// drill: open machine keeps period
await page.goto(`${BASE}/factory?plant=${plant.id}&period=yesterday`);
await page.getByTestId("map-marker").first().click();
await page.getByTestId("open-machine").waitFor({ timeout: 10000 });
const href = await page.getByTestId("open-machine").getAttribute("href");
check(/period=yesterday/.test(href ?? ""), "summary 'abrir máquina' keeps period=yesterday", href);

const relevant = errors.filter((e) => !/favicon|ResizeObserver|WebSocket|ws\/sensors|1011/.test(e));
check(relevant.length === 0, "no console errors (besides the forced WebSocket drop)", relevant.slice(0, 5));
await browser.close();
console.log(`\n${failures === 0 ? "OK" : "FAIL"} (${failures} failure(s))`);
process.exit(failures === 0 ? 0 : 1);
