/**
 * Sprint 5 fix loop — gateway fleet UI against a real API (seed_dev data, no edge process needed):
 *  * install wizard shows ONE command: docker run with SERVER_URL + ENROLL_CODE and the release image (no token,
 *    no MQTT password, no curl) + the infra/gateway compose alternative;
 *  * the seeded Modbus tags (address "unit=U;hr|di=N[:type]") of Robô de Solda are assigned through the UI and the
 *    config map carries integer register + unit/area;
 *  * an enrolled gateway must be revoked before removal; revoking shows which broker line to remove; then removal.
 *
 *   BASE_URL=http://localhost:5250 API=http://localhost:8150 node e2e/sprint5/gateway_fix.verify.mjs
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import fs from "node:fs";
const BASE = process.env.BASE_URL ?? "http://localhost:5250";
const API = process.env.API ?? "http://localhost:8150";
const OUT = process.env.OUT ?? new URL("../screenshots/sprint5", import.meta.url).pathname;
fs.mkdirSync(OUT, { recursive: true });
let failures = 0;
const check = (c, m, got) => { console.log(`  ${c ? "✓" : "✗"} ${m}${c || got === undefined ? "" : `   got=${JSON.stringify(got).slice(0, 300)}`}`); if (!c) failures += 1; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function api(method, p, token, body) {
  const r = await fetch(`${API}${p}`, { method, headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, data: await r.json().catch(() => null) };
}
const admin = (await api("POST", "/api/auth/login", null, { email: "admin@factory-monitor.com", password: "Admin@1234" })).data.access_token;
const tree = (await api("GET", "/api/plants/tree", admin)).data;
const machines = Object.fromEntries(tree.flatMap((p) => p.lines.flatMap((l) => l.machines.map((m) => [m.name, m.id]))));
const allTags = (await api("GET", "/api/tags/?limit=1000", admin)).data;
const robot = allTags.filter((t) => t.machine_id === machines["Robô de Solda"] && t.protocol === "modbus_tcp");
console.log("robot modbus tags:", robot.map((t) => t.address));
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
const name = `Fix gw ${Date.now().toString(36)}`;
await page.goto(`${BASE}/settings/gateways`);
await page.getByRole("button", { name: "Adicionar gateway" }).first().click();
await page.fill("#gw-name", name);
await page.getByRole("button", { name: "Criar e gerar código" }).click();
const code = (await page.getByTestId("enroll-code").innerText({ timeout: 15000 })).trim();
const run = await page.getByTestId("cmd-run").innerText();
check(run.includes(`ENROLL_CODE=${code}`) && run.includes(`SERVER_URL=${BASE}`) && run.includes("factoryconnect-edge:0.5.0"), "wizard: one-step docker run with ENROLL_CODE, SERVER_URL and the release image", run);
check(!/GATEWAY_TOKEN|MQTT_PASSWORD|curl/.test(run), "wizard: no token / MQTT password / curl in the command", run);
check((await page.getByTestId("cmd-compose").innerText()).includes("gateway.env"), "wizard: compose alternative");
await page.screenshot({ path: `${OUT}/gwfix-wizard.png` });
await page.getByRole("button", { name: "Atribuir tags e ver saúde" }).click();
await sleep(1000);
const gw = (await api("GET", "/api/gateways/", admin)).data.find((g) => g.name === name);
await api("PATCH", `/api/gateways/${gw.id}`, admin, { devices: [{ id: "modbus-plc", protocol: "modbus", host: "127.0.0.1", port: 5020 }] });
await page.reload();
await page.getByTestId("gw-tags").waitFor({ timeout: 15000 });
const robotSet = page.getByTestId("gw-tags").locator("fieldset", { hasText: "Robô de Solda" });
const boxes = robotSet.locator('input[type="checkbox"]');
const n = await boxes.count();
for (let i = 0; i < n; i += 1) await boxes.nth(i).check();
await page.getByRole("button", { name: "Salvar atribuição" }).click();
const msg = await page.getByTestId("gw-tags").locator('[role="alert"], [role="status"]').first().innerText({ timeout: 15000 }).catch(() => "");
check(n === robot.length && /atribuída/.test(msg), `UI assigns the ${robot.length} seeded Modbus tags of Robô de Solda (${msg.split("\n")[0]})`, msg);
await page.screenshot({ path: `${OUT}/gwfix-modbus-assign.png` });
const prev = (await api("GET", `/api/gateways/${gw.id}/config-preview`, admin)).data;
check(prev.plant_map.tags.length === robot.length && prev.plant_map.tags.every((t) => Number.isInteger(t.address) && t.unit_id >= 0 && t.register_type), "config map: integer address + unit/area for each Modbus tag", prev.plant_map.tags.map((t) => [t.address, t.unit_id, t.register_type, t.data_type_plc]));
// enroll (as the device would) → revoke from the UI
const enr = await api("POST", "/api/gateways/enroll", null, { code });
check(enr.status === 200, "device enrolled", enr.status);
await page.reload();
await page.getByTestId("gw-remove-hint").waitFor({ timeout: 15000 });
check(await page.getByRole("button", { name: "Remover gateway" }).count() === 0, "enrolled gateway: remove needs revoke first");
await page.getByRole("button", { name: "Revogar gateway" }).click();
const notice = await page.getByTestId("gw-mqtt-revoked").innerText({ timeout: 15000 }).catch(() => "");
check(notice.includes(enr.data.mqtt.username) && notice.includes("gateways.conf"), "revoked: notice tells which broker line to remove", notice);
await page.screenshot({ path: `${OUT}/gwfix-revoked.png` });
await page.getByRole("button", { name: "Remover gateway" }).click();
await sleep(1500);
const left = (await api("GET", "/api/gateways/", admin)).data.find((g) => g.id === gw.id);
check(!left, "revoked gateway removed");
check(errors.length === 0, "no console errors", errors);
await browser.close();
console.log(failures ? `FAIL ${failures}` : "ALL OK");
process.exit(failures ? 1 : 0);
