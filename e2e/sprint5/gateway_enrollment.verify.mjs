/**
 * Gateway fleet end to end (Sprint 5, acceptance e): UI → enrollment code → REAL edge gateway process enrolls with
 * ENROLL_CODE → fetches its remote config → reads the virtual plant (OPC UA) → MQTT → server → UI "Online";
 * config changed on the server (more tags) → the running gateway hot-reloads (same PID, counters continue);
 * gateway stopped → UI "Offline". Also probes assigning a seeded Modbus tag (address "unit=U;hr=N" format).
 *
 * Needs: a server (API) with seed_dev data whose MQTT broker is BROKER_PORT, vite (BASE_URL) proxied to it, and a
 * live virtual plant serving OPC UA on VP_OPCUA_PORT.
 *
 *   BASE_URL=http://localhost:5261 API=http://localhost:8161 BROKER_PORT=18852 VP_OPCUA_PORT=8200 \
 *     node e2e/sprint5/gateway_enrollment.verify.mjs
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const BASE = process.env.BASE_URL ?? "http://localhost:5261";
const API = process.env.API ?? "http://localhost:8161";
const BROKER_PORT = process.env.BROKER_PORT ?? "18852";
const VP_OPCUA_PORT = process.env.VP_OPCUA_PORT ?? "8200";
const REPO = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../..");
const OUT = process.env.OUT ?? path.join(REPO, "e2e/screenshots/sprint5");
const WORK = fs.mkdtempSync(path.join(os.tmpdir(), "qa-gw-"));
fs.mkdirSync(OUT, { recursive: true });
let failures = 0;
const check = (c, m, got) => { console.log(`  ${c ? "✓" : "✗"} ${m}${c || got === undefined ? "" : `   got=${JSON.stringify(got).slice(0, 300)}`}`); if (!c) failures += 1; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function api(method, p, token, body) {
  const r = await fetch(`${API}${p}`, { method, headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, data: await r.json().catch(() => null) };
}
async function until(fn, ms, step = 2000) {
  const t0 = Date.now();
  for (;;) { const v = await fn(); if (v) return v; if (Date.now() - t0 > ms) return v; await sleep(step); }
}

const admin = (await api("POST", "/api/auth/login", null, { email: "admin@factory-monitor.com", password: "Admin@1234" })).data.access_token;
const tree = (await api("GET", "/api/plants/tree", admin)).data;
const machines = Object.fromEntries(tree.flatMap((p) => p.lines.flatMap((l) => l.machines.map((m) => [m.name, m.id]))));
const allTags = (await api("GET", "/api/tags/?limit=1000", admin)).data;
const tagsOf = (name, proto) => allTags.filter((t) => t.machine_id === machines[name] && (!proto || t.protocol === proto));
const name = `QA gateway ${Date.now().toString(36)}`;

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "pt-BR", timezoneId: "America/Manaus" });
const page = await ctx.newPage();
const errors = [];
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
page.on("pageerror", (e) => errors.push(String(e)));
page.on("dialog", (d) => d.accept());
await page.goto(`${BASE}/login`);
await page.fill('input[type="email"]', "admin@factory-monitor.com");
await page.fill('input[type="password"]', "Admin@1234");
await page.click('button[type="submit"]');
await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });

// 1. wizard
await page.goto(`${BASE}/settings/gateways`);
await page.getByRole("button", { name: "Adicionar gateway" }).first().click();
await page.fill("#gw-name", name);
await page.getByRole("button", { name: "Criar e gerar código" }).click();
const code = (await page.getByTestId("enroll-code").innerText({ timeout: 15000 })).trim();
check(/^[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(code), `UI wizard shows a one-time enrollment code (${code})`);
await page.screenshot({ path: `${OUT}/gw-e2e-wizard.png` });
await page.getByRole("button", { name: "Atribuir tags e ver saúde" }).click();
await sleep(1500);
const gw = (await api("GET", "/api/gateways/", admin)).data.find((g) => g.name === name);
check(gw && gw.status === "pending", "API lists the new gateway as pending", gw);

// 2. devices (UI): OPC UA endpoint of the virtual plant + a Modbus device
await page.getByRole("button", { name: "Adicionar dispositivo" }).click();
await page.fill("#dev-id-0", "opcua-plc");
await page.selectOption("#dev-p-0", "opcua");
await page.fill("#dev-ep-0", `opc.tcp://127.0.0.1:${VP_OPCUA_PORT}/factoryconnect/virtual-plant/`);
await page.getByRole("button", { name: "Adicionar dispositivo" }).click();
await page.fill("#dev-id-1", "modbus-plc");
await page.fill("#dev-h-1", "127.0.0.1");
await page.fill("#dev-po-1", String(Number(VP_OPCUA_PORT) + 1));
await page.getByRole("button", { name: "Salvar dispositivos" }).click();
await sleep(1500);
const gwDev = (await api("GET", `/api/gateways/${gw.id}`, admin)).data;
check((gwDev.devices ?? []).length === 2, "UI saved 2 PLC devices", gwDev.devices);

// 3. tag assignment (UI): Torno CNC (OPC UA)
const torno = tagsOf("Torno CNC", "opc_ua");
const boxes = page.getByTestId("gw-tags").locator("fieldset", { hasText: "Torno CNC" }).locator('input[type="checkbox"]');
const nBoxes = await boxes.count();
for (let i = 0; i < nBoxes; i += 1) await boxes.nth(i).check();
await page.getByRole("button", { name: "Salvar atribuição" }).click();
const okMsg = await page.getByTestId("gw-tags").getByRole("status").innerText({ timeout: 15000 }).catch(() => "");
check(nBoxes === torno.length && /atribuída/.test(okMsg), `UI assigns the ${torno.length} Torno CNC OPC UA tags (${okMsg.split("\n")[0]})`, okMsg);
const v1 = (await api("GET", `/api/gateways/${gw.id}`, admin)).data.config_version;

// 3b. Modbus probe (seeded address format "unit=U;di=N"): assign one robot tag through the UI
const robotTag = tagsOf("Robô de Solda", "modbus_tcp")[0];
const robotBox = page.getByTestId("gw-tags").locator("label", { hasText: robotTag.address }).locator('input[type="checkbox"]').first();
await robotBox.check();
await page.getByRole("button", { name: "Salvar atribuição" }).click();
await sleep(2000);
const modbusRes = await page.getByTestId("gw-tags").locator('[role="alert"], [role="status"]').first().innerText().catch(() => "");
check(/atribuída/.test(modbusRes), `UI can assign a seeded Modbus tag (${robotTag.name} @ ${robotTag.address})`, modbusRes);
await page.screenshot({ path: `${OUT}/gw-e2e-modbus-assign.png` });
// back to the OPC UA-only set (API) so the run below is deterministic
const put = await api("PUT", `/api/gateways/${gw.id}/tags`, admin, { tags: torno.map((t) => ({ tag_id: t.id })) });
check(put.status === 200, "OPC UA-only tag set restored", put);
const vStart = (await api("GET", `/api/gateways/${gw.id}`, admin)).data.config_version;

// 4. real edge gateway enrolls with ENROLL_CODE and runs
const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !/^(MQTT_|GATEWAY_|PLANT_MAP|SERVER_URL|ENROLL|TENANT_ID)/.test(k)));
Object.assign(env, {
  SERVER_URL: API, ENROLL_CODE: code, GATEWAY_CREDENTIALS_PATH: `${WORK}/creds.json`, GATEWAY_CONFIG_CACHE: `${WORK}/cfg.json`,
  GATEWAY_HEARTBEAT_S: "5", GATEWAY_CONFIG_POLL_S: "15", MQTT_BROKER_HOST: "127.0.0.1", MQTT_BROKER_PORT: BROKER_PORT,
  MQTT_TLS: "false", BUFFER_DB_PATH: `${WORK}/buf.db`, COUNTER_DB_PATH: `${WORK}/cnt.db`, ENCRYPTION_KEY: "",
  EDGE_VERSION: "qa-0.5.0", HEALTH_FILE: `${WORK}/health`, LOG_LEVEL: "INFO",
});
const log = fs.openSync(`${WORK}/gw.log`, "w");
const gwProc = spawn(`${REPO}/.venv-edge/bin/python`, ["-m", "edge.src.gateway"], { cwd: REPO, env, stdio: ["ignore", log, log] });
const pid = gwProc.pid;
const readLog = () => fs.readFileSync(`${WORK}/gw.log`, "utf8");
const enrolled = await until(() => /enrollment\.done/.test(readLog()), 40000, 1000);
check(enrolled, "edge gateway enrolled with ENROLL_CODE", readLog().slice(-600));
check(enrolled && (fs.statSync(`${WORK}/creds.json`).mode & 0o777) === 0o600, "credentials file is 0600");
const cfgOk = await until(() => /edge_gateway\.config\b.*origin=remote/.test(readLog()), 40000, 1000);
check(cfgOk, "gateway loaded its remote config from the server", readLog().slice(-800));
check(!/fcg_[A-Za-z0-9]{8,}/.test(readLog()), "gateway log never prints the device token");
const online = await until(async () => { const g = (await api("GET", `/api/gateways/${gw.id}`, admin)).data; return g.status === "online" ? g : null; }, 60000);
check(online && online.version === "qa-0.5.0" && online.config_version === vStart, "API: gateway online, version and config version from the heartbeat", online);
// data flows: Torno CNC readings reach the server
const tornoCounter = torno.find((t) => /cycle|part/.test(t.semantic_role ?? "")) ?? torno[0];
async function lastPoint(tagId) {
  const start = new Date(Date.now() - 180000).toISOString();
  const r = await api("GET", `/api/sensors/history?tag_id=${tagId}&start=${encodeURIComponent(start)}&window=5s`, admin);
  const pts = r.data?.points ?? [];
  return pts.length ? pts[pts.length - 1] : null;
}
const latest = () => lastPoint(tornoCounter.id);
const firstVal = await until(async () => { const l = await latest(); return l && l.value != null && Date.now() - Date.parse(l.timestamp) < 120000 ? l : null; }, 90000);
check(firstVal, `data flows: ${tornoCounter.name} has a fresh reading on the server`, firstVal);
await page.goto(`${BASE}/settings/gateways`);
const cardOnline = await until(async () => /Online/i.test(await page.locator("main").innerText()), 30000, 2000);
check(cardOnline, "UI fleet list shows the gateway Online");
await page.screenshot({ path: `${OUT}/gw-e2e-online.png` });

// 5. config change on the server → hot reload (same process), counters continue
const centro = tagsOf("Centro de Usinagem", "opc_ua");
const before = await latest();
const put2 = await api("PUT", `/api/gateways/${gw.id}/tags`, admin, { tags: [...torno, ...centro].map((t) => ({ tag_id: t.id })) });
check(put2.status === 200 && put2.data.config_version === vStart + 1, `config change bumps config_version to ${vStart + 1}`, put2.data);
const reloaded = await until(() => /config_reloaded/.test(readLog()), 60000, 1000);
check(reloaded && gwProc.exitCode === null && gwProc.pid === pid, "running gateway hot-reloaded the new config (same PID, no restart)", readLog().split("\n").filter((l) => /config_/.test(l)).slice(-3));
const g2 = await until(async () => { const g = (await api("GET", `/api/gateways/${gw.id}`, admin)).data; return g.last_heartbeat?.config_version === vStart + 1 ? g : null; }, 30000);
check(g2, "heartbeat reports the new config version", g2?.last_heartbeat);
const centroCounter = centro.find((t) => /cycle|part/.test(t.semantic_role ?? "")) ?? centro[0];
const centroVal = await until(() => lastPoint(centroCounter.id), 60000);
check(centroVal, "new tags flow after the reload (Centro de Usinagem)", centroVal);
await sleep(8000);
const after = await latest();
check(before && after && Number(after.value) >= Number(before.value), `counter continues across the reload (${before?.value} → ${after?.value})`, { before, after });

// 6. stop the gateway → offline
gwProc.kill("SIGTERM");
await until(() => gwProc.exitCode !== null, 20000, 500);
check(gwProc.exitCode !== null, `gateway stopped (exit ${gwProc.exitCode})`);
const offline = await until(async () => { const g = (await api("GET", `/api/gateways/${gw.id}`, admin)).data; return g.status === "offline" ? g : null; }, 150000, 5000);
check(offline, "API: gateway offline after it stops", offline?.status);
await page.reload();
const cardOffline = await until(async () => /Offline/i.test(await page.locator("main").innerText()), 30000, 2000);
check(cardOffline, "UI shows the gateway Offline");
await page.screenshot({ path: `${OUT}/gw-e2e-offline.png` });
check(errors.length === 0, `zero console errors ${errors.join(" | ").slice(0, 300)}`);
await browser.close();
console.log(`work dir: ${WORK}`);
console.log(failures ? `${failures} check(s) failed` : "all checks passed");
process.exit(failures ? 1 : 0);
