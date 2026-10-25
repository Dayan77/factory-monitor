/**
 * Sprint 6 acceptance 6 (docs/sprint6.md §4/§6.6): a NEW tenant follows the onboarding wizard IN THE BROWSER
 * against the virtual plant and reaches step 9 all green with 15 min of continuous data.
 *
 *   - superadmin creates an empty tenant (API); its admin logs in and walks /onboarding/1 … /onboarding/9
 *   - step 2: asset CSV (built from the virtual plant's own map) previewed + confirmed in the UI; output machines
 *   - step 3: "3 turnos" template; step 4: products/PRI via the API (settings page, outside the wizard)
 *   - step 5: "Criar gateway e gerar código" → the code SHOWN IN THE UI enrolls a REAL edge gateway process
 *   - step 6: spec block `s6-tags-csv` previews with 0 errors; `invalid_csv_row` shows the edge parser's message
 *     on file line 8; OPC UA browse through the real gateway lists the virtual plant's nodes; the plant's full
 *     tags CSV is confirmed
 *   - step 8: a second admin is added in the form; step 9 is polled until every required check is green
 *
 * Needs: API (server with MQTT on the local broker), vite BASE_URL proxied to it, a virtual plant on VP_OPCUA_PORT /
 * VP_MODBUS_PORT (`python -m edge.virtual_plant --opcua-port … --modbus-port …`).
 *
 *   BASE_URL=http://localhost:5173 API=http://localhost:8000 VP_OPCUA_PORT=8170 VP_MODBUS_PORT=8171 \
 *     STATE=/tmp/onb_state.json node e2e/sprint6/onboarding_new_tenant.verify.mjs
 *
 * The gateway is left running (pid in STATE) so later checks (map) can use the tenant; kill it afterwards.
 * READY_WAIT_MIN (default 22) bounds the wait for step 9.
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import { spawn, execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const BASE = process.env.BASE_URL ?? "http://localhost:5173";
const API = process.env.API ?? "http://localhost:8000";
const VP_OPCUA = process.env.VP_OPCUA_PORT ?? "8170";
const VP_MODBUS = process.env.VP_MODBUS_PORT ?? "8171";
const READY_WAIT_MIN = Number(process.env.READY_WAIT_MIN ?? 22);
const REPO = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../..");
const OUT = process.env.OUT ?? path.join(REPO, "e2e/screenshots/sprint6");
const STATE = process.env.STATE ?? path.join(os.tmpdir(), "qa_s6_onboarding_state.json");
const WORK = fs.mkdtempSync(path.join(os.tmpdir(), "qa-s6-onb-"));
fs.mkdirSync(OUT, { recursive: true });
let failures = 0;
const check = (c, m, got) => { console.log(`  ${c ? "✓" : "✗"} ${m}${c || got === undefined ? "" : `   got=${JSON.stringify(got).slice(0, 400)}`}`); if (!c) failures += 1; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function api(method, p, token, body) {
  const r = await fetch(`${API}${p}`, { method, headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, data: await r.json().catch(() => null) };
}
async function until(fn, ms, step = 2000) {
  const t0 = Date.now();
  for (;;) { const v = await fn(); if (v) return v; if (Date.now() - t0 > ms) return v; await sleep(step); }
}

// ── spec blocks ─────────────────────────────────────────────────────────────
const spec = fs.readFileSync(path.join(REPO, "docs/sprint6.md"), "utf8");
const block = (name) => JSON.parse(spec.match(new RegExp("```json " + name + "\\n([\\s\\S]*?)\\n```"))[1]);
const tagsSpec = block("s6-tags-csv");

// ── virtual plant map → asset CSV + tags CSV (what an integrator would export from the PLC project) ────────────
const mapPath = path.join(WORK, "vp_map.json");
execFileSync(path.join(REPO, ".venv-edge/bin/python"), ["-m", "edge.virtual_plant", "--write-map", mapPath], { cwd: REPO, stdio: "ignore" });
const vpMap = JSON.parse(fs.readFileSync(mapPath, "utf8"));
const q = (v) => (v === null || v === undefined ? "" : /[;"\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
const assetRows = ["linha;maquina;modelo;serie;saida;posicao;tipo;fabricante;modelo_componente;serie_componente;vida_ciclos;vida_horas;vida_partidas;ciclos_por_peca"];
for (const ln of vpMap.assets.lines) for (const m of ln.machines) {
  const out = ln.output_machine === m.name ? "sim" : "não";
  if (!m.positions?.length) assetRows.push([ln.name, m.name, m.model, m.serial_number, out, "", "", "", "", "", "", "", "", ""].map(q).join(";"));
  for (const p of m.positions ?? []) {
    const c = p.component ?? {}; const nl = c.nominal_life ?? {};
    assetRows.push([ln.name, m.name, m.model, m.serial_number, out, p.name, p.component_type, c.manufacturer, c.model, c.serial_number, nl.cycles, nl.hours, nl.starts, p.cycles_per_part].map(q).join(";"));
  }
}
const assetsCsv = assetRows.join("\n");
const tagHeader = tagsSpec.csv[0];
const tagRows = [tagHeader];
for (const t of vpMap.tags) {
  tagRows.push([t.key, t.line, t.machine, t.position, t.semantic_role, t.source, t.address, t.unit_id, t.register_type,
    t.data_type_plc, t.word_order, t.counter_max, t.unit, t.scale, t.offset, t.poll_interval_ms].map(q).join(";"));
}
const tagsCsv = tagRows.join("\n");

// ── tenant ──────────────────────────────────────────────────────────────────
const stamp = Date.now().toString(36);
const pw = `Qa!${crypto.randomBytes(9).toString("base64url")}`;
const adminEmail = `qa-onb-${stamp}@example.com`;
const sa = (await api("POST", "/api/auth/login", null, { email: "superadmin@factory-monitor.com", password: "Super@1234" })).data.access_token;
const tenant = await api("POST", "/api/admin/tenants", sa, { name: `QA Implantação ${stamp}`, slug: `qa-s6-onb-${stamp}`, max_users: 20, max_machines: 50, admin_email: adminEmail, admin_password: pw, admin_name: "Admin QA Implantação" });
check(tenant.status === 201, "superadmin creates an empty tenant", tenant);
const tok = (await api("POST", "/api/auth/login", null, { email: adminEmail, password: pw })).data.access_token;
const state = { tenant_id: tenant.data.id, admin_email: adminEmail, admin_password: pw, work: WORK };
const save = () => fs.writeFileSync(STATE, JSON.stringify(state, null, 2), { mode: 0o600 });
save();

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const ctx = await browser.newContext({ viewport: { width: 1366, height: 768 }, locale: "pt-BR", timezoneId: "America/Manaus", colorScheme: "light" });
const page = await ctx.newPage();
const errors = [];
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
page.on("pageerror", (e) => errors.push(String(e)));
page.on("dialog", (d) => d.accept());
await page.goto(`${BASE}/login`);
await page.fill('input[type="email"]', adminEmail);
await page.fill('input[type="password"]', pw);
await page.click('button[type="submit"]');
await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });

const progress = async () => (await api("GET", "/api/onboarding", tok)).data;
const stepState = async (n) => (await progress()).steps.find((s) => s.step === n)?.state;
async function done(n) {
  await page.getByTestId("step-done").click();
  const st = await until(async () => (await stepState(n)) === "done" && "done", 15000, 500);
  check(st === "done", `step ${n} marked done through the wizard`, await stepState(n));
}
const shot = (n) => page.screenshot({ path: path.join(OUT, `onboarding_step${n}.png`), fullPage: true });

// step 1
await page.goto(`${BASE}/onboarding/1`);
await page.fill("#ob-name", "Planta Manaus (QA)");
await page.fill("#ob-address", vpMap.assets.plant.address);
await page.selectOption("#ob-uf", "AM");
check((await page.inputValue("#ob-tz")) === "America/Manaus", "UF AM suggests America/Manaus", await page.inputValue("#ob-tz"));
await page.getByTestId("step-done").click();
check(await page.getByText(/confirm/i).first().isVisible(), "without the timezone confirmation step 1 is blocked on the client");
check((await stepState(1)) !== "done", "step 1 not done without confirmation", await stepState(1));
await page.getByTestId("tz-confirm").check();
await shot(1);
await done(1);
const plantId = (await progress()).plant_id;
state.plant_id = plantId; save();
check(!!plantId, "step 1 created the plant");

// step 2: assets CSV
await page.goto(`${BASE}/onboarding/2`);
await page.getByTestId("csv-text-assets").fill(assetsCsv);
await page.getByTestId("csv-preview").click();
await page.getByTestId("import-preview").waitFor({ timeout: 15000 });
const aRows = await page.getByTestId("preview-row").evaluateAll((els) => els.map((e) => e.dataset.status));
check(aRows.length === assetRows.length - 1 && aRows.every((s) => s === "ok"), `asset CSV preview: ${assetRows.length - 1} rows ok`, aRows);
await page.getByTestId("csv-confirm").click();
const tree = await until(async () => { const t = (await api("GET", "/api/plants/tree", tok)).data; const p = t?.find((x) => x.id === plantId); return p && p.lines.length === vpMap.assets.lines.length && p; }, 15000, 500);
check(!!tree, "asset CSV confirmed: lines created", tree);
await page.reload();
const outSet = tree?.lines.every((l) => l.output_machine_id);
if (!outSet) {
  for (const ln of vpMap.assets.lines) {
    const l = tree.lines.find((x) => x.name === ln.name); const m = l.machines.find((x) => x.name === ln.output_machine);
    await page.locator("[data-testid=ob-lines] li", { hasText: ln.name }).locator("select").selectOption(m.id);
    await sleep(800);
  }
}
const tree2 = (await api("GET", "/api/plants/tree", tok)).data.find((x) => x.id === plantId);
check(tree2.lines.every((l) => l.output_machine_id), `output machine on every line (${outSet ? "from CSV 'saida'" : "set in the wizard"})`, tree2.lines.map((l) => l.output_machine_id));
await shot(2);
await done(2);
const machineId = Object.fromEntries(tree2.lines.flatMap((l) => l.machines.map((m) => [m.name, m.id])));
state.machines = machineId; save();

// step 3
await page.goto(`${BASE}/onboarding/3`);
await page.getByRole("button", { name: /3 turnos/ }).click();
await page.getByTestId("ob-shifts").waitFor({ timeout: 15000 });
await shot(3);
await done(3);

// step 4: products + PRI (Configurações → Produtos e ciclos is outside the wizard; done via the same API it uses)
await page.goto(`${BASE}/onboarding/4`);
const prods = {};
for (const p of vpMap.assets.products) prods[p.code] = (await api("POST", "/api/products/", tok, { code: p.code, name: p.name })).data.id;
for (const ln of vpMap.assets.lines) for (const m of ln.machines) {
  for (const mp of m.products ?? []) await api("POST", `/api/machines/${machineId[m.name]}/products`, tok, { product_id: prods[mp.product], ideal_cycle_time_s: mp.ideal_cycle_time_s, parts_per_cycle: mp.parts_per_cycle, is_default: mp.is_default });
  if (m.fault_code_map) await api("PATCH", `/api/machines/${machineId[m.name]}`, tok, { fault_code_map: m.fault_code_map });
}
await page.reload();
await page.getByText(/PRI do produto padrão cadastrado/).waitFor({ timeout: 15000 }).catch(() => {});
check(await page.getByText(/PRI do produto padrão cadastrado/).isVisible(), "step 4 shows the PRI check green");
await shot(4);
await done(4);

// step 5: gateway created in the UI; the code shown on screen enrolls the real edge gateway
await page.goto(`${BASE}/onboarding/5`);
await page.fill("#ob-gw", "Gateway QA planta virtual");
await page.getByTestId("ob-create-gw").click();
const code = (await page.getByTestId("enroll-code").textContent({ timeout: 15000 })).trim();
check(/^[A-Z0-9-]{6,}$/.test(code), "enrollment code shown", code);
const gw = (await api("GET", `/api/gateways/?plant_id=${plantId}`, tok)).data[0];
state.gateway_id = gw.id; save();
const devs = [{ id: "opcua-plc", protocol: "opcua", endpoint: `opc.tcp://127.0.0.1:${VP_OPCUA}/factoryconnect/virtual-plant/` },
  { id: "modbus-plc", protocol: "modbus", host: "127.0.0.1", port: Number(VP_MODBUS) }];
const pd = await api("PATCH", `/api/gateways/${gw.id}`, tok, { devices: devs, heartbeat_s: 10 });
check(pd.status === 200, "gateway sources (OPC UA + Modbus of the virtual plant) set", pd);
const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !/^(MQTT_|GATEWAY_|PLANT_MAP|SERVER_URL|ENROLL|TENANT_ID|VIRTUAL_PLANT)/.test(k)));
Object.assign(env, { SERVER_URL: API, ENROLL_CODE: code, GATEWAY_CREDENTIALS_PATH: `${WORK}/creds.json`, GATEWAY_CONFIG_CACHE: `${WORK}/cfg.json`,
  GATEWAY_HEARTBEAT_S: "10", MQTT_BROKER_HOST: "127.0.0.1", MQTT_BROKER_PORT: "1883", MQTT_TLS: "false", BUFFER_DB_PATH: `${WORK}/buf.db`,
  COUNTER_DB_PATH: `${WORK}/cnt.db`, ENCRYPTION_KEY: "", LOG_LEVEL: "INFO" });
const gwProc = spawn(path.join(REPO, ".venv-edge/bin/python"), ["-m", "edge.src.gateway"], { cwd: REPO, env, detached: true, stdio: ["ignore", fs.openSync(`${WORK}/gw.log`, "w"), fs.openSync(`${WORK}/gw.log`, "a")] });
gwProc.unref();
state.gateway_pid = gwProc.pid; state.gateway_log = `${WORK}/gw.log`; save();
console.log(`  gateway pid ${gwProc.pid} log ${WORK}/gw.log`);
const online = await until(async () => page.getByTestId("ob-gateways").getByText(/online/i).isVisible().catch(() => false), 60000, 1000);
check(online, "UI shows the gateway online (first heartbeat of the real gateway)");
await shot(5);
await done(5);

// step 6: spec CSV (preview, nothing written), invalid row on line 8, OPC UA browse, then the plant's CSV
await page.goto(`${BASE}/onboarding/6`);
await page.getByTestId("csv-text-tags").fill(tagsSpec.csv.join("\n"));
await page.getByTestId("csv-preview").click();
await page.getByTestId("import-preview").waitFor({ timeout: 15000 });
let st = await page.getByTestId("preview-row").evaluateAll((els) => els.map((e) => e.dataset.status));
check(st.length === 6 && st.every((s) => s === "ok"), "s6-tags-csv: 6 rows, no error", st);
check(await page.getByTestId("csv-confirm").isEnabled(), "s6-tags-csv can be confirmed");
await page.screenshot({ path: path.join(OUT, "onboarding_step6_spec_csv.png"), fullPage: true });
await page.getByTestId("csv-text-tags").fill([...tagsSpec.csv, tagsSpec.invalid_csv_row].join("\n"));
await page.getByTestId("csv-preview").click();
const errRow = page.locator('[data-testid=preview-row][data-status=error]');
await errRow.first().waitFor({ timeout: 15000 });
const errText = await errRow.first().innerText();
check(await errRow.count() === 1 && /^\s*8\b/.test(errText) && errText.includes(tagsSpec.invalid_error_contains), `cycle_pulse on holding: edge error on line 8 ("${tagsSpec.invalid_error_contains}")`, errText);
check(!(await page.getByTestId("csv-confirm").isEnabled()), "confirm disabled while a row has errors");
const confirmWithErr = await api("POST", "/api/onboarding/tags/import", tok, { gateway_id: gw.id, csv: [...tagsSpec.csv, tagsSpec.invalid_csv_row].join("\n"), confirm: true });
check(confirmWithErr.status === 422, "API: confirm with an error row → 422, nothing written", confirmWithErr.status);
await page.screenshot({ path: path.join(OUT, "onboarding_step6_invalid_row.png"), fullPage: true });

// OPC UA browse via the real gateway
await page.getByRole("tab", { name: "Navegar OPC UA" }).click();
await page.getByTestId("browse-panel").waitFor();
const t0 = Date.now();
await page.getByTestId("browse-start").click();
await until(async () => /conclu|done|\d+ nó/i.test((await page.getByTestId("browse-status").innerText().catch(() => "")) || "") && (await page.getByTestId("browse-node").count()) > 0, 90000, 1000);
const nNodes = await page.getByTestId("browse-node").count();
const bStatus = await page.getByTestId("browse-status").innerText().catch(() => "");
const nodeNames = await page.locator('[aria-label="Nós do servidor OPC UA"]').innerText().catch(() => "");
check(nNodes > 0 && /run_state/.test(nodeNames), `OPC UA browse through the real gateway lists the virtual plant nodes (${nNodes} variables, ${((Date.now() - t0) / 1000).toFixed(1)} s)`, { bStatus, nodeNames: nodeNames.slice(0, 300) });
await page.screenshot({ path: path.join(OUT, "onboarding_step6_browse.png"), fullPage: true });
const cmds = await api("GET", `/api/gateways/${gw.id}`, tok);
void cmds;

// full plant tags
await page.getByRole("tab", { name: "CSV de tags" }).click();
await page.getByTestId("csv-text-tags").fill(tagsCsv);
await page.getByTestId("csv-preview").click();
await page.getByTestId("import-preview").waitFor({ timeout: 15000 });
st = await page.getByTestId("preview-row").evaluateAll((els) => els.map((e) => e.dataset.status));
const bad = await page.locator('[data-testid=preview-row][data-status=error]').allInnerTexts();
check(st.length === vpMap.tags.length && bad.length === 0, `plant tags CSV: ${vpMap.tags.length} rows, no error`, bad.slice(0, 5));
await page.getByTestId("csv-confirm").click();
const tagCount = await until(async () => { const g = (await api("GET", `/api/gateways/?plant_id=${plantId}`, tok)).data[0]; return g.tag_count === vpMap.tags.length && g.tag_count; }, 20000, 1000);
check(tagCount === vpMap.tags.length, `confirmed: ${vpMap.tags.length} tags assigned to the gateway`, tagCount);
const reimport = await api("POST", "/api/onboarding/tags/import", tok, { gateway_id: gw.id, csv: tagsCsv, confirm: false });
check(reimport.status === 200 && reimport.data.rows.every((r) => r.status === "update"), "re-import preview: every row 'update' (idempotent)", reimport.data?.summary);
await shot(6);
await done(6);

// step 7, 8
await page.goto(`${BASE}/onboarding/7`);
await shot(7);
await done(7);
await page.goto(`${BASE}/onboarding/8`);
await page.fill("#ob-u-name", "Segunda Admin QA");
await page.fill("#ob-u-email", `qa-onb2-${stamp}@example.com`);
await page.selectOption("#ob-u-role", "admin");
await page.fill("#ob-u-pass", `Qa!${crypto.randomBytes(9).toString("base64url")}`);
await page.getByRole("button", { name: "Adicionar usuário" }).click();
await page.getByText("Segunda Admin QA").waitFor({ timeout: 10000 });
await shot(8);
await done(8);

// step 9: wait for 15 min of continuous data
await page.goto(`${BASE}/onboarding/9`);
const tReady = Date.now();
let rd;
for (;;) {
  rd = (await api("GET", `/api/onboarding/readiness?plant_id=${plantId}`, tok)).data;
  const pend = rd.checks.filter((c) => c.required && !c.ok).map((c) => `${c.id}: ${c.detail.slice(0, 120)}`);
  console.log(`  … ${((Date.now() - tReady) / 60000).toFixed(1)} min, ready=${rd.ready} ${pend.join(" | ")}`);
  if (rd.ready || Date.now() - tReady > READY_WAIT_MIN * 60000) break;
  await sleep(30000);
}
check(rd.ready, `readiness: every required check green (after ${((Date.now() - tReady) / 60000).toFixed(1)} min)`, rd.checks.filter((c) => !c.ok));
await page.reload();
await page.locator('[data-testid=readiness] li').first().waitFor({ timeout: 20000 });
const statuses = await page.locator('[data-testid=readiness] li').evaluateAll((els) => els.map((e) => [e.dataset.check, e.dataset.status]));
const reqIds = rd.checks.filter((c) => c.required).map((c) => c.id);
check(statuses.filter(([id]) => reqIds.includes(id)).every(([, s]) => s === "ok"), "UI: every required check rendered green", statuses);
await page.getByTestId("step-done").click();
const fin = await until(async () => { const p = await progress(); return p.completed && p; }, 15000, 500);
check(!!fin && !!fin.ready_at, "step 9 done: onboarding completed (ready_at set)", fin);
await page.screenshot({ path: path.join(OUT, "onboarding_step9_green.png"), fullPage: true });
await page.emulateMedia({ colorScheme: "dark" });
await page.screenshot({ path: path.join(OUT, "onboarding_step9_green_dark.png"), fullPage: true });
await page.goto(`${BASE}/onboarding`);
await sleep(1500);
console.log(`  reopen /onboarding → ${page.url()}`);

const relevant = errors.filter((e) => !/favicon|ResizeObserver/.test(e));
check(relevant.length === 0, "no console errors", relevant.slice(0, 5));
await browser.close();
console.log(`\n${failures === 0 ? "OK" : "FAIL"} (${failures} failure(s)); state ${STATE}; gateway pid ${gwProc.pid} still running`);
process.exit(failures === 0 ? 0 : 1);
