/**
 * Sprint 5 screens against a REAL running API, seeded with scripts/seed_dev.py plus golden scenario F1
 * (stroke time of a cylinder drifting until "crítico") and the server started with AI_PROVIDER=scripted.
 *
 *   BASE_URL=http://localhost:5250 API=http://localhost:8150 node e2e/sprint5.real.verify.mjs
 *
 * Checks, desktop 1440×900 and phone 390×844, light and dark, zero console errors:
 *  - planner: /maintenance/predictive (alerts by severity, detail with chart/band/limits/EWMA/CUSUM/RUL, evidence,
 *    "Gerar OS preditiva"), monitors tab (learning + monitoring), Raio-X predictive card, position drawer "Condição"
 *  - /ai: suggestions, streamed answer with "consultando" steps and citations to the exact screen, history
 *  - admin: /settings/gateways wizard (enrollment code + commands), device enrollment + heartbeat through the device
 *    API → the card turns "Online" live; tag assignment
 *  - technician (phone): the predictive WO shows the alert text and chart; completion with finding "sem defeito"
 *    closes the alert as false alarm (P-8/P-9), checked through the API
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.BASE_URL ?? "http://localhost:5250";
const API = process.env.API ?? "http://localhost:8150";
const OUT = process.env.OUT ?? "e2e/screenshots/sprint5-real";
const THEMES = (process.env.THEMES ?? "light,dark").split(",");
fs.mkdirSync(OUT, { recursive: true });
let failures = 0;
function check(cond, msg) {
  if (cond) console.log(`  ✓ ${msg}`);
  else { failures += 1; console.log(`  ✗ ${msg}`); }
}

async function api(method, p, token, body, headers = {}) {
  const r = await fetch(`${API}${p}`, { method, headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}), ...headers }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, data: await r.json().catch(() => null) };
}
const token = async (email, password) => (await api("POST", "/api/auth/login", null, { email, password })).data.access_token;

async function newPage(browser, viewport, theme, mobile = false) {
  const context = await browser.newContext({ viewport, colorScheme: theme, hasTouch: mobile, isMobile: mobile, timezoneId: "America/Manaus", locale: "pt-BR" });
  await context.addInitScript((t) => { try { localStorage.setItem("fm-theme", t); } catch { /* */ } }, theme);
  const page = await context.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  return { context, page, errors };
}

async function login(page, email, password) {
  await page.goto(`${BASE}/login`);
  await page.getByLabel(/e-mail/i).fill(email);
  await page.getByLabel(/senha/i).fill(password);
  await page.getByRole("button", { name: /entrar/i }).click();
  await page.waitForURL(/dashboard|\/m\//, { timeout: 20_000 });
}

async function noHorizontalScroll(page, label) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check(over <= 1, `${label}: no horizontal page scroll (${over}px)`);
}

const PLANNER = ["planejador@factory-monitor.com", process.env.PLANNER_PASSWORD ?? "Planner@1234"];
const ADMIN = ["admin@factory-monitor.com", process.env.ADMIN_PASSWORD ?? "Admin@1234"];
const TECHN = ["tecnico@factory-monitor.com", process.env.TECH_PASSWORD ?? "Tecnico@1234"];

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
try {
  const ptok = await token(...PLANNER);
  const alerts = (await api("GET", "/api/predictive/alerts?state=open&state=acknowledged&state=converted", ptok)).data;
  check(Array.isArray(alerts) && alerts.length > 0, `API has open predictive alerts (${alerts?.length})`);
  const target = alerts.find((a) => a.state !== "converted") ?? alerts[0];

  for (const theme of THEMES) {
    // ── planner desktop ──
    console.log(`\n[${theme}] planner desktop`);
    {
      const { context, page, errors } = await newPage(browser, { width: 1440, height: 900 }, theme);
      await login(page, ...PLANNER);
      // Raio-X card
      const card = page.getByTestId("raiox-predictive");
      await card.waitFor({ timeout: 20_000 });
      await card.getByRole("link").nth(1).waitFor({ timeout: 20_000 });
      check(await card.getByText(/crítico|alerta|atenção/i).count() > 0, "Raio-X: 'O que está degradando' lists the open alert");
      await page.screenshot({ path: path.join(OUT, `raiox-${theme}.png`), fullPage: true });

      await page.goto(`${BASE}/maintenance/predictive`);
      await page.getByTestId("pred-alert-card").first().waitFor({ timeout: 20_000 });
      const first = page.getByTestId("pred-alert-card").first();
      check((await first.getAttribute("data-severity")) === "critical", "alerts: critical first");
      check(/Falha em \d+ dias/.test(await first.innerText()), "alerts: card shows the condition RUL");
      await page.screenshot({ path: path.join(OUT, `predictive-alerts-${theme}.png`), fullPage: true });

      await page.goto(`${BASE}/maintenance/predictive?alert=${target.id}`);
      const detail = page.getByTestId("alert-detail");
      await detail.waitFor({ timeout: 20_000 });
      await detail.getByTestId("condition-chart").waitFor({ timeout: 20_000 });
      check((await detail.getByTestId("alert-explanation").innerText()).includes("referência 200,0 ± 2,0 ms"), "detail: server explanation verbatim (m0 ± σ)");
      check(await detail.locator(".recharts-reference-line").count() >= 2, "detail chart: failure limit + rule lines");
      check(await detail.locator(".recharts-area").count() >= 1, "detail chart: m0 ± 3σ band");
      check(await detail.getByText("EWMA (em σ)").count() === 1 && await detail.getByText("CUSUM (em σ)").count() === 1, "detail: EWMA and CUSUM charts");
      check(await detail.getByText("Projeção da tendência").count() >= 1, "detail: RUL projection series in the legend");
      check(await detail.getByTestId("alert-evidence").locator("li").count() >= 3, "detail: evidence list");
      await page.screenshot({ path: path.join(OUT, `predictive-detail-${theme}.png`) });
      if (theme === THEMES[0] && target.state !== "converted") {
        await detail.getByRole("button", { name: "Gerar OS preditiva" }).click();
        await page.getByLabel(/Horas estimadas/).fill("2");
        await page.getByTestId("alert-actions").getByRole("button", { name: "Gerar OS preditiva" }).click();
        await page.getByText(/OS preditiva OS-\d+ criada e aprovada/).waitFor({ timeout: 20_000 }).then(() => check(true, "convert → predictive WO created"), () => check(false, "convert → predictive WO created"));
        await detail.getByRole("link", { name: /OS-\d+/ }).waitFor({ timeout: 10_000 }).then(() => check(true, "detail links the WO"), () => check(false, "detail links the WO"));
      }
      await page.keyboard.press("Escape");

      await page.goto(`${BASE}/maintenance/predictive?tab=monitors`);
      await page.getByTestId("monitor-row").first().waitFor({ timeout: 20_000 });
      const rows = await page.getByTestId("monitor-row").allInnerTexts();
      check(rows.some((r) => /aprendendo \(\d+\/20 janelas\)/.test(r)), "monitors: learning state with progress");
      check(rows.some((r) => r.includes("Monitorando")), "monitors: monitoring state");
      await page.screenshot({ path: path.join(OUT, `predictive-monitors-${theme}.png`), fullPage: true });
      await page.getByTestId("monitor-row").filter({ hasText: "Monitorando" }).first().click();
      await page.getByTestId("monitor-config").waitFor({ timeout: 20_000 });
      await page.getByTestId("rule-row").first().waitFor();
      await page.screenshot({ path: path.join(OUT, `predictive-monitor-sheet-${theme}.png`) });
      await page.keyboard.press("Escape");

      await page.goto(`${BASE}/maintenance/predictive?tab=evaluation`);
      await page.getByTestId("eval-precision").waitFor({ timeout: 20_000 }).then(() => check(true, "evaluation tab loads"), () => check(false, "evaluation tab loads"));
      await page.screenshot({ path: path.join(OUT, `predictive-evaluation-${theme}.png`), fullPage: true });

      // position drawer → Condição
      await page.goto(`${BASE}/factory/machines/${target.machine_id}?pos=${target.position_id}`);
      const tab = page.getByRole("tab", { name: "Condição" });
      await tab.waitFor({ timeout: 20_000 });
      await tab.click();
      await page.getByRole("dialog").getByTestId("condition-chart").waitFor({ timeout: 20_000 }).then(() => check(true, "drawer: Condição tab shows the chart"), () => check(false, "drawer: Condição tab shows the chart"));
      await page.screenshot({ path: path.join(OUT, `drawer-condition-${theme}.png`) });

      // AI assistant
      await page.goto(`${BASE}/ai`);
      await page.getByTestId("suggestions").waitFor({ timeout: 20_000 });
      await page.getByLabel("Pergunta").fill("O que está parado agora?");
      await page.getByLabel("Pergunta").press("Enter");
      const msg = page.getByTestId("assistant-message").last();
      await msg.getByTestId("answer-sources").waitFor({ timeout: 30_000 }).then(() => check(true, "ai: streamed answer with sources"), () => check(false, "ai: streamed answer with sources"));
      const href = await msg.getByTestId("citation-ref").first().getAttribute("href").catch(() => null);
      check(!!href && href.startsWith("/"), `ai: citation links to a screen (${href})`);
      check(await msg.getByText(/fonte|Consultou/).count() > 0, "ai: tool steps visible");
      await page.waitForURL(/\/ai\?c=/, { timeout: 10_000 }).then(() => check(true, "ai: conversation id in the URL"), () => check(false, "ai: conversation id in the URL"));
      await page.getByTestId("conversation-list").first().waitFor({ timeout: 10_000 }).then(() => check(true, "ai: history sidebar lists the conversation"), () => check(false, "ai: history sidebar"));
      await page.screenshot({ path: path.join(OUT, `ai-${theme}.png`) });
      // a catalog-style citation (API params) opens the screen with the same filters
      await page.goto(`${BASE}/oee?scope=line&id=${target.line_id}&start=2026-10-01T04:00:00Z&stop=2026-10-03T04:00:00Z&group_by=shift`);
      await page.waitForURL(/period=custom/, { timeout: 20_000 }).then(() => check(true, "deep link: /oee start/stop/group_by → custom period"), () => check(false, "deep link: /oee start/stop/group_by → custom period"));
      check(/from=2026-10-01&to=2026-10-02&group=shift/.test(page.url()), `deep link: local days + group (${page.url().split("?")[1]})`);

      check(errors.length === 0, `planner (${theme}): zero console errors${errors.length ? ` — ${errors.slice(0, 3).join(" | ")}` : ""}`);
      await context.close();
    }

    // ── admin: gateways ──
    console.log(`\n[${theme}] admin gateways`);
    {
      const { context, page, errors } = await newPage(browser, { width: 1440, height: 900 }, theme);
      await login(page, ...ADMIN);
      await page.goto(`${BASE}/settings/gateways`);
      await page.getByRole("heading", { name: "Gateways de campo", exact: true }).first().waitFor({ timeout: 20_000 });
      await page.getByRole("button", { name: "Adicionar gateway" }).first().click();
      const name = `GW e2e ${theme} ${Date.now() % 100000}`;
      await page.getByLabel(/Nome/).fill(name);
      await page.getByRole("button", { name: "Criar e gerar código" }).click();
      const code = (await page.getByTestId("enroll-code").innerText({ timeout: 20_000 })).trim();
      check(/^[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(code), `wizard: enrollment code shown (${code})`);
      const runCmd = await page.getByTestId("cmd-run").innerText();
      // fix loop: one-step install — the gateway enrolls itself with ENROLL_CODE; no token/MQTT password/curl
      check(runCmd.includes(`ENROLL_CODE=${code}`) && runCmd.includes("SERVER_URL=") && !/GATEWAY_TOKEN|MQTT_PASSWORD|curl /.test(runCmd),
        "wizard: single docker run command with SERVER_URL + ENROLL_CODE (no token, no MQTT password)");
      await page.screenshot({ path: path.join(OUT, `gateways-wizard-${theme}.png`) });
      // the device enrolls itself and sends a heartbeat (device API, no JWT)
      const en = await api("POST", "/api/gateways/enroll", null, { code });
      check(en.status === 200 && !!en.data?.token, "device: enroll with the code returns its token");
      const hb = await api("POST", `/api/gateways/${en.data.gateway_id}/heartbeat`, null, { version: "e2e-1.0", uptime_s: 3600, buffer_depth: 0, errors: [] }, { authorization: `Gateway ${en.data.token}` });
      check(hb.status === 200, `device: heartbeat accepted (${hb.status})`);
      await page.getByRole("button", { name: "Atribuir tags e ver saúde" }).click();
      const health = page.getByTestId("gw-health");
      await health.getByText("Online").waitFor({ timeout: 20_000 }).then(() => check(true, "detail: status Online after the heartbeat (live)"), () => check(false, "detail: status Online after the heartbeat"));
      check((await health.innerText()).includes("e2e-1.0"), "detail: edge version from the heartbeat");
      await page.getByTestId("gw-tags").waitFor({ timeout: 20_000 }).catch(() => undefined);
      await page.screenshot({ path: path.join(OUT, `gateways-detail-${theme}.png`) });
      await page.keyboard.press("Escape");
      await page.getByTestId("gw-card").filter({ hasText: name }).waitFor({ timeout: 20_000 });
      check((await page.getByTestId("gw-card").filter({ hasText: name }).getAttribute("data-status")) === "online", "list: card online");
      await page.screenshot({ path: path.join(OUT, `gateways-list-${theme}.png`), fullPage: true });
      check(errors.length === 0, `admin (${theme}): zero console errors${errors.length ? ` — ${errors.slice(0, 3).join(" | ")}` : ""}`);
      await context.close();
    }

    // ── phone: planner screens + AI ──
    console.log(`\n[${theme}] phone 390px`);
    {
      const { context, page, errors } = await newPage(browser, { width: 390, height: 844 }, theme, true);
      await login(page, ...PLANNER);
      await page.goto(`${BASE}/maintenance/predictive`);
      await page.getByTestId("pred-alert-card").first().waitFor({ timeout: 20_000 });
      await noHorizontalScroll(page, "predictive (phone)");
      await page.screenshot({ path: path.join(OUT, `phone-predictive-${theme}.png`), fullPage: true });
      await page.getByTestId("pred-alert-card").first().click();
      await page.getByTestId("alert-detail").getByTestId("condition-chart").waitFor({ timeout: 20_000 });
      await page.screenshot({ path: path.join(OUT, `phone-predictive-detail-${theme}.png`) });
      await page.keyboard.press("Escape");
      await page.goto(`${BASE}/ai`);
      await page.getByLabel("Pergunta").waitFor({ timeout: 20_000 });
      await noHorizontalScroll(page, "ai (phone)");
      await page.screenshot({ path: path.join(OUT, `phone-ai-${theme}.png`) });
      await page.goto(`${BASE}/settings/gateways`);
      await page.waitForTimeout(1500);
      await noHorizontalScroll(page, "gateways (phone)");
      check(errors.length === 0, `phone (${theme}): zero console errors${errors.length ? ` — ${errors.slice(0, 3).join(" | ")}` : ""}`);
      await context.close();
    }
  }

  // ── /ai without a provider (status intercepted): explicit empty state, no composer ──
  console.log("\n[light] ai not configured (mocked status)");
  {
    const { context, page, errors } = await newPage(browser, { width: 1440, height: 900 }, "light");
    await login(page, ...PLANNER);
    await page.route("**/api/ai/status", (r) => r.fulfill({ json: { enabled: true, configured: false, provider: null, model: null, daily_token_budget: 0, tokens_used_today: 0, rate_limit_per_min: 20, max_tool_calls: 6 } }));
    await page.goto(`${BASE}/ai`);
    await page.getByTestId("ai-not-configured").waitFor({ timeout: 20_000 }).then(() => check(true, "ai: not-configured empty state"), () => check(false, "ai: not-configured empty state"));
    check(await page.getByLabel("Pergunta").count() === 0, "ai: no composer without provider");
    await page.screenshot({ path: path.join(OUT, "ai-not-configured.png") });
    check(errors.length === 0, `ai not configured: zero console errors${errors.length ? ` — ${errors.slice(0, 3).join(" | ")}` : ""}`);
    await context.close();
  }

  // ── technician executes the predictive WO on the phone ──
  console.log("\n[light] technician predictive WO");
  {
    const conv = (await api("GET", `/api/predictive/alerts/${target.id}`, ptok)).data;
    const woId = conv.work_order_id;
    check(!!woId, "alert has the predictive WO");
    const techTok = await token(...TECHN);
    const me = (await api("GET", "/api/auth/me", techTok)).data;
    const today = new Date().toLocaleDateString("en-CA", { timeZone: "America/Manaus" });
    const sch = await api("POST", `/api/work-orders/${woId}/transition`, ptok, { to: "scheduled", scheduled_for: today, assignee_id: me.id });
    check(sch.status === 200, `planner schedules the WO for the technician (${sch.status})`);
    const { context, page, errors } = await newPage(browser, { width: 390, height: 844 }, "light", true);
    await login(page, ...TECHN);
    await page.goto(`${BASE}/m/work-orders/${woId}`);
    const block = page.getByTestId("wo-predictive");
    await block.waitFor({ timeout: 20_000 });
    check((await block.getByTestId("wo-alert-text").innerText()).includes("tempo de curso"), "phone WO: alert text");
    await block.getByTestId("condition-chart").waitFor({ timeout: 20_000 }).then(() => check(true, "phone WO: condition chart"), () => check(false, "phone WO: condition chart"));
    await page.screenshot({ path: path.join(OUT, "tech-predictive-wo.png"), fullPage: true });
    await page.getByRole("button", { name: "Iniciar" }).click();
    await page.getByText("OS iniciada.").waitFor({ timeout: 20_000 });
    await page.getByRole("button", { name: "Concluir" }).click();
    const dlg = page.getByRole("dialog", { name: "Concluir OS" });
    await dlg.getByRole("button", { name: "Concluir OS" }).click();
    check(await dlg.getByText("Informe o achado da inspeção preditiva.").count() === 1, "completion asks for the finding");
    await dlg.getByText(/Sem defeito/).click();
    await page.screenshot({ path: path.join(OUT, "tech-complete-finding.png") });
    await dlg.getByRole("button", { name: "Concluir OS" }).click();
    await page.getByText("OS concluída.").first().waitFor({ timeout: 20_000 }).then(() => check(true, "WO completed"), () => check(false, "WO completed"));
    const after = (await api("GET", `/api/predictive/alerts/${target.id}`, ptok)).data;
    check(after.state === "closed" && after.resolution === "false_alarm", `alert closed as false alarm (${after.state}/${after.resolution})`);
    check(errors.length === 0, `technician: zero console errors${errors.length ? ` — ${errors.slice(0, 3).join(" | ")}` : ""}`);
    await context.close();
  }
} finally {
  await browser.close();
}
console.log(failures ? `\n${failures} check(s) FAILED` : "\nall checks passed");
process.exit(failures ? 1 : 0);
