/**
 * Sprint 4 acceptance (docs/maintenance_workflows.md §7, items 1–6) against the REAL API seeded with golden
 * scenario E and the maintenance clock frozen at `evaluate_at` (2026-12-01 12:00Z = 08:00 Manaus).
 *
 * Setup (see e2e/sprint4/README in the QA report):
 *   1. cd server && FM_TEST_DB=fm_test_qa_e FM_TEST_KEEP_DB=1 ../.venv/bin/python -m pytest -q tests/test_maintenance_scenario_e.py
 *   2. cd server && set -a && . ../.env && set +a && POSTGRES_DB=fm_test_qa_e ../.venv/bin/python ../e2e/sprint4/fixed_clock_server.py --port 8110
 *   3. cd web && API_PROXY_TARGET=http://localhost:8110 npx vite --port 5210
 *   4. TENANT_EMAIL_SUFFIX=<suffix of the scenario E users, e.g. "45cf58db"> is not needed: users are looked up
 *      by role through PLANNER/TECH/... env vars:
 *      PLANNER=<email> TECH=<email> ADMIN=<email> SUPERVISOR=<email> MANAGER=<email> node e2e/sprint4/scenario_e_ui.verify.mjs
 *
 * The browser clock is frozen at evaluate_at too (Playwright clock), so "today", the month and the 12-month
 * window are the scenario's. Scenario WO numbers (OS-1xx) are renumbered per tenant by the server (OS-0xx);
 * they are mapped by title.
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import fs from "node:fs";

const BASE = process.env.BASE_URL ?? "http://localhost:5210";
const API = process.env.API ?? "http://localhost:8110";
const OUT = process.env.OUT ?? "e2e/screenshots/sprint4";
const PW = process.env.PASSWORD ?? "Test@12345";
const AT = new Date("2026-12-01T12:00:00Z");
const E = JSON.parse(fs.readFileSync(new URL("../../docs/scenarios/scenario_e_maintenance.json", import.meta.url)));
const X = E.expected;
fs.mkdirSync(OUT, { recursive: true });

let failures = 0;
function check(cond, msg, extra) {
  if (cond) console.log(`  ✓ ${msg}`);
  else { failures += 1; console.log(`  ✗ ${msg}${extra !== undefined ? ` — got ${JSON.stringify(extra)}` : ""}`); }
}
const close = (a, b, tol = 1e-6) => (a == null || b == null ? a === b : Math.abs(a - b) <= tol * Math.max(1, Math.abs(b)));

async function api(method, p, token, body, extraHeaders = {}) {
  const r = await fetch(`${API}${p}`, { method, headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}), ...extraHeaders }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, data: await r.json().catch(() => null) };
}
const login = async (email) => {
  const r = await api("POST", "/api/auth/login", null, { email, password: PW });
  if (r.status !== 200) throw new Error(`login ${email}: ${r.status} ${JSON.stringify(r.data)}`);
  return r.data.access_token;
};
const T = {
  planner: await login(process.env.PLANNER), tech: await login(process.env.TECH), admin: await login(process.env.ADMIN),
  supervisor: await login(process.env.SUPERVISOR), manager: await login(process.env.MANAGER),
};

async function newPage(browser, viewport, mobile = false) {
  const context = await browser.newContext({ viewport, hasTouch: mobile, isMobile: mobile, timezoneId: "America/Manaus", locale: "pt-BR" });
  await context.clock.setFixedTime(AT);
  const page = await context.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  return { context, page, errors };
}
async function uiLogin(page, email) {
  await page.goto(`${BASE}/login`);
  await page.getByLabel(/e-mail/i).fill(email);
  await page.getByLabel(/senha/i).fill(PW);
  await page.getByRole("button", { name: /entrar/i }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20_000 });
}

// ── scenario id ↔ server id maps ─────────────────────────────────────────────
const plans = (await api("GET", "/api/maintenance-plans/status", T.planner)).data;
const planId = {};
for (const p of E.plans) planId[p.id] = plans.find((r) => r.plan_name === p.name)?.plan_id;
const allWos = (await api("GET", "/api/work-orders/?limit=500", T.planner)).data;
const woList = Array.isArray(allWos) ? allWos : allWos.items;
const woByTitle = Object.fromEntries(woList.map((w) => [w.title, w]));
const wo = {};
for (const w of E.work_orders) wo[w.id] = woByTitle[w.title];
console.log(`mapped ${Object.values(planId).filter(Boolean).length}/6 plans, ${Object.values(wo).filter(Boolean).length}/${E.work_orders.length} WOs`);

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
try {
  // ── (1) Próximas manutenções ──────────────────────────────────────────────
  console.log("\n[1] Próximas manutenções = expected.plans (API + UI)");
  for (const [pid, exp] of Object.entries(X.plans)) {
    const r = plans.find((x) => x.plan_id === planId[pid]);
    check(r && r.state === exp.state && close(r.consumed, exp.consumed) && r.determining_trigger === exp.consumed_trigger
      && r.due_trigger === exp.due_trigger && r.next_due_date === exp.next_due_date
      && JSON.stringify(r.next_due_usage_by_measure) === JSON.stringify(Object.fromEntries(Object.entries(exp.next_due_usage).map(([k, v]) => [k, v]))),
      `API ${pid}: ${exp.state}, ${exp.consumed.toFixed(4)} (${exp.consumed_trigger}), vence ${exp.next_due_date} por ${exp.due_trigger}`,
      r && { state: r.state, consumed: r.consumed, t: r.determining_trigger, due: r.due_trigger, d: r.next_due_date, u: r.next_due_usage_by_measure });
  }
  const TRIG = { cycles: "ciclos", hours: "horas", starts: "partidas", days: "calendário" };
  const STATE = { ok: "Em dia", due_soon: "Vence em breve", due: "Vencido", overdue: "Atrasado" };
  const fmtPct = (v) => `${(v * 100).toFixed(1).replace(".", ",")} %`;
  const fmtDate = (d) => d.split("-").reverse().join("/");
  {
    const { context, page, errors } = await newPage(browser, { width: 1440, height: 1000 });
    await uiLogin(page, process.env.PLANNER);
    await page.goto(`${BASE}/maintenance/upcoming`);
    await page.getByTestId("plan-row").first().waitFor({ timeout: 20_000 });
    const rows = page.getByTestId("plan-row");
    check(await rows.count() === 6, "UI lists the six plans", await rows.count());
    for (const [pid, exp] of Object.entries(X.plans)) {
      const row = page.locator(`[data-testid="plan-row"][data-plan="${planId[pid]}"]`);
      const txt = (await row.innerText()).replace(/\s+/g, " ");
      const okState = txt.includes(STATE[exp.state]);
      const okPct = txt.includes(fmtPct(exp.consumed).replace(" %", "")) && txt.includes(`(${TRIG[exp.consumed_trigger]})`);
      const okDate = exp.past_due ? /Vencido/.test(txt) : txt.includes(fmtDate(exp.next_due_date)) && txt.includes(`por ${TRIG[exp.due_trigger]}`);
      const nu = Object.entries(exp.next_due_usage);
      const okUsage = nu.length === 0 ? true : nu.some(([, v]) => txt.includes(new Intl.NumberFormat("pt-BR").format(v)));
      check(okState && okPct && okDate && okUsage, `UI ${pid}: "${txt.slice(0, 160)}"`, { okState, okPct, okDate, okUsage });
    }
    const pl3 = (await page.locator(`[data-testid="plan-row"][data-plan="${planId.PL3}"]`).innerText()).replace(/\s+/g, " ");
    check(/82,0 ?%?\(partidas\)/.test(pl3.replace(/\s/g, "")) || pl3.includes("82,0") && pl3.includes("(partidas)"), "PL3 shows 82 % partidas");
    check(pl3.includes("10/01/2027") && pl3.includes("por horas"), "PL3 vence 10/01/2027 por horas (not 15/01)");
    await page.screenshot({ path: `${OUT}/e1-upcoming.png`, fullPage: true });
    check(errors.length === 0, "no console errors on Próximas manutenções", errors);

    // ── (4) rejected transitions: UI never offers the button ────────────────
    console.log("\n[4] rejected transitions → 422 (API) and no button (UI)");
    const LABELS = { completed: /^Concluir$/, in_progress: /^(Iniciar|Retomar|Assumir)$/, cancelled: /^(Cancelar OS|Descartar)$/ };
    for (const rj of E.rejected_transitions) {
      const w = wo[rj.wo];
      const cur = (await api("GET", `/api/work-orders/${w.id}`, T.admin)).data;
      if (cur.state !== rj.from) {
        console.log(`  · ${rj.wo} is now ${cur.state} (scenario end state), from-state ${rj.from} checked by the API test only`);
        continue;
      }
      const r = await api("POST", `/api/work-orders/${w.id}/transition`, T.admin, { to: rj.to, ...(rj.body ?? {}) });
      check(r.status === 422 && Array.isArray(r.data?.detail?.allowed), `API ${rj.wo} ${rj.from}→${rj.to}${rj.body ? " (no reason)" : ""}: 422 with allowed=${JSON.stringify(r.data?.detail?.allowed)}`, r.status);
      for (const who of ["admin", "planner"]) {
        // deep link opens the detail panel
        if (who === "admin") { /* planner session is the page above; admin uses its own context below */ }
      }
      await page.goto(`${BASE}/maintenance/work-orders/${w.id}`);
      const group = page.getByRole("group", { name: "Ações da OS" });
      await page.getByText(w.number).first().waitFor({ timeout: 15_000 });
      await page.waitForTimeout(800);
      const labels = (await group.count()) ? await group.getByRole("button").allInnerTexts() : [];
      if (rj.body && rj.to === "cancelled") {
        // cancel is allowed, but only with a reason: the form must not submit without one
        const btn = group.getByRole("button", { name: /Cancelar OS/ });
        check(await btn.count() === 1, `UI ${rj.wo} (${w.number}) offers Cancelar OS (allowed with reason)`, labels);
        await btn.click();
        const form = page.getByRole("form", { name: /Cancelar OS/i }).or(page.locator("form").filter({ hasText: /Motivo/ })).first();
        let posted = false;
        const onReq = (req) => { if (req.method() === "POST" && req.url().includes(`/work-orders/${w.id}/transition`)) posted = true; };
        page.on("request", onReq);
        const submit = form.getByRole("button", { name: /Confirmar|Cancelar OS|Salvar/ }).last();
        const disabled = await submit.isDisabled().catch(() => false);
        if (!disabled) await submit.click().catch(() => {});
        await page.waitForTimeout(600);
        page.off("request", onReq);
        check(!posted, `UI ${rj.wo}: cancel without a reason is not submitted (button disabled=${disabled})`);
        await page.screenshot({ path: `${OUT}/e4-${rj.wo}-cancel-needs-reason.png` });
      } else {
        check(!labels.some((l) => LABELS[rj.to].test(l.trim())), `UI ${rj.wo} (${w.number}, ${rj.from}) hides ${rj.to}: buttons=${JSON.stringify(labels)}`);
      }
    }
    // open → in_progress only for an emergency corrective: fresh WOs, technician view (desktop + mobile)
    const mot = plans.find((p) => p.plan_id === planId.PL3);
    const normal = await api("POST", "/api/work-orders/", T.planner, { type: "corrective", title: "QA: OS aberta não emergencial", estimated_hours: 1, position_id: mot.position_id });
    const ncur = normal.data;
    check(normal.status === 201, `planner opens a non-emergency corrective (state ${ncur?.state})`, normal.status);
    if (ncur.state === "open") {
      const r = await api("POST", `/api/work-orders/${ncur.id}/transition`, T.tech, { to: "in_progress" });
      check(r.status === 422, `API technician open→in_progress on a non-emergency WO: 422`, r.status);
    }
    await context.close();

    const tech = await newPage(browser, { width: 360, height: 740 }, true);
    await uiLogin(tech.page, process.env.TECH);
    await tech.page.goto(`${BASE}/m/work-orders/${ncur.id}`);
    await tech.page.getByText(ncur.number).first().waitFor({ timeout: 15_000 });
    await tech.page.waitForTimeout(800);
    check(await tech.page.getByRole("button", { name: /^(Assumir chamado|Iniciar)$/ }).count() === 0, `mobile: no Assumir/Iniciar on a non-emergency open WO (${ncur.number})`);
    await tech.page.screenshot({ path: `${OUT}/e4-mobile-open-non-emergency.png` });
    await tech.context.close();
    // cleanup: discard it so it does not stay in the backlog
    await api("POST", `/api/work-orders/${ncur.id}/transition`, T.planner, { to: "cancelled", cancel_reason: "duplicate" });
  }

  // ── (3) cancel a ≥ 80 % draft → exactly one new draft (UI) ────────────────
  console.log("\n[3] cancelling a ≥ 80 % plan draft re-creates exactly one draft (S4-4)");
  {
    const before = (await api("GET", "/api/maintenance-plans/status", T.planner)).data.find((p) => p.plan_id === planId.PL3);
    const draftId = before.open_work_order_id;
    check(before.consumed >= 0.8 && before.open_work_order_state === "draft", `PL3 at ${before.consumed} has draft ${before.open_work_order_number}`);
    const { context, page, errors } = await newPage(browser, { width: 1440, height: 1000 });
    await uiLogin(page, process.env.PLANNER);
    await page.goto(`${BASE}/maintenance/work-orders/${draftId}`);
    const group = page.getByRole("group", { name: "Ações da OS" });
    await group.waitFor({ timeout: 15_000 });
    await group.getByRole("button", { name: /Descartar|Cancelar OS/ }).click();
    const form = page.locator("form").filter({ has: page.locator("select, input[type=radio]") }).last();
    const sel = form.locator("select");
    if (await sel.count()) {
      const opts = await sel.first().locator("option").evaluateAll((os) => os.map((o) => o.value).filter(Boolean));
      await sel.first().selectOption(opts.includes("plan_reprogrammed") ? "plan_reprogrammed" : opts[0]);
    } else {
      await form.locator("input[type=radio]").first().check();
    }
    await form.getByRole("button").filter({ hasText: /Confirmar|Descartar|Cancelar OS/ }).last().click();
    await page.waitForTimeout(2500);
    await page.screenshot({ path: `${OUT}/e3-draft-cancelled.png` });
    const old = (await api("GET", `/api/work-orders/${draftId}`, T.planner)).data;
    check(old.state === "cancelled", `old draft ${old.number} cancelled (reason ${old.cancel_reason})`, old.state);
    const after = (await api("GET", "/api/maintenance-plans/status", T.planner)).data.find((p) => p.plan_id === planId.PL3);
    check(after.open_work_order_id && after.open_work_order_id !== draftId && after.open_work_order_state === "draft", `new draft ${after.open_work_order_number} for PL3`);
    const open = woList.length >= 0 && (await api("GET", `/api/work-orders/?plan_id=${planId.PL3}&limit=100`, T.planner)).data;
    const openList = (Array.isArray(open) ? open : open.items).filter((w) => !["completed", "closed", "cancelled"].includes(w.state));
    check(openList.length === 1, `exactly one open WO for PL3`, openList.map((w) => `${w.number}:${w.state}`));
    await api("POST", "/api/maintenance-plans/recompute", T.planner);
    const again = (await api("GET", `/api/work-orders/?plan_id=${planId.PL3}&limit=100`, T.planner)).data;
    check((Array.isArray(again) ? again : again.items).filter((w) => !["completed", "closed", "cancelled"].includes(w.state)).length === 1, "still one after a recompute");
    check(errors.length === 0, "no console errors", errors);
    await context.close();
  }

  // ── (5) Gestão da manutenção (November) ───────────────────────────────────
  console.log("\n[5] Gestão da manutenção — November");
  {
    const k = (await api("GET", `/api/maintenance/kpis?start=${E.report_period.start_utc}&stop=${E.report_period.stop_utc}`, T.manager)).data;
    check(close(k.pmp, X.pmp.pmp) && close(k.schedule_compliance, X.schedule_compliance.compliance), `API PMP ${k.pmp} / compliance ${k.schedule_compliance}`);
    check(close(k.backlog_hours, 18) && close(k.backlog_weeks, 0.45) && k.overdue_count === 3, `API backlog ${k.backlog_hours} h / ${k.backlog_weeks} wk / ${k.overdue_count} overdue`);
    const ex = k.compliance_excluded.map((e) => [e.wo_number, e.reason]);
    const expEx = X.schedule_compliance.excluded.map(([id, reason]) => [wo[id].number, reason]);
    check(JSON.stringify(ex) === JSON.stringify(expEx), `API excluded ${JSON.stringify(ex)} = OS-115/OS-121 ${JSON.stringify(expEx)}`);

    const { context, page, errors } = await newPage(browser, { width: 1440, height: 1000 });
    await uiLogin(page, process.env.MANAGER);
    await page.goto(`${BASE}/maintenance/kpis`);
    await page.getByText("Fora do cumprimento da programação").waitFor({ timeout: 20_000 });
    await page.waitForTimeout(1000);
    const txt = (await page.locator("main").innerText()).replace(/\s+/g, " ");
    check(txt.includes("Mês de 11/2026"), "period is November 2026");
    check(txt.includes("68,4 %") || txt.includes("0,684211"), "PMP 0,684211 shown (68,4 %)");
    check(txt.includes("50,0 %") || txt.includes("0,500000"), "compliance 0,500000 shown (50,0 %)");
    check(/18,0 ?h/.test(txt), "backlog 18,0 h");
    check(txt.includes("0,45 semana"), "backlog 0,45 semana", txt.match(/[\d,]+ semana\(s\)/)?.[0]);
    check(/OS vencidas 3/.test(txt), "3 OS vencidas");
    check(txt.includes(`${wo["OS-115"].number} cancelada como substituída`), `${wo["OS-115"].number} (= OS-115) excluded: cancelada como substituída`);
    check(txt.includes(`${wo["OS-121"].number} encaixe`), `${wo["OS-121"].number} (= OS-121) excluded: encaixe — programada depois do congelamento`);
    await page.screenshot({ path: `${OUT}/e5-kpis.png`, fullPage: true });
    check(errors.length === 0, "no console errors", errors);
    await context.close();
  }

  // ── (6) Confiabilidade (12 months) ────────────────────────────────────────
  console.log("\n[6] Confiabilidade — 12 months");
  {
    const { context, page, errors } = await newPage(browser, { width: 1440, height: 1000 });
    await uiLogin(page, process.env.PLANNER);
    await page.goto(`${BASE}/maintenance/reliability`);
    await page.getByRole("table").last().waitFor({ timeout: 20_000 });
    await page.waitForTimeout(1500);
    const head = (await page.locator("main").innerText()).replace(/\s+/g, " ");
    check(head.includes("01/12/2025 – 30/11/2026"), "window 01/12/2025 – 30/11/2026");
    const nf = (v, d = 0) => new Intl.NumberFormat("pt-BR", { maximumFractionDigits: d }).format(v);
    const mttr = (m) => { const h = Math.floor(m / 60), mm = Math.floor(m - h * 60); return h ? `${h} h${mm ? ` ${mm} min` : ""}` : `${Math.round(m)} min`; };
    async function tableRows() {
      return (await page.getByRole("table").last().locator("tbody tr").allInnerTexts()).map((t) => t.replace(/\s+/g, " ").trim());
    }
    const posName = { "POS-A1": "Cilindro A1", "POS-A2": "Cilindro A2", "POS-MOT": "Motor do transportador", "POS-AX1": "Eixo 1" };
    let rows = await tableRows();
    for (const [pid, exp] of Object.entries(X.reliability_by_position)) {
      const r = rows.find((t) => t.startsWith(posName[pid]));
      const ok = r && r.includes(` ${exp.n} `) && r.includes(`${nf(exp.mtbf_h)} h`) && (exp.mcbf_cycles == null || r.includes(nf(exp.mcbf_cycles))) && r.includes(mttr(exp.mttr_min))
        && r.endsWith(`${exp.restored} ${exp.incipient}`);
      check(ok, `position ${pid}: n=${exp.n} MTBF ${nf(exp.mtbf_h)} h MCBF ${exp.mcbf_cycles ?? "—"} MTTR ${mttr(exp.mttr_min)} restored ${exp.restored} incipient ${exp.incipient}`, r);
    }
    await page.screenshot({ path: `${OUT}/e6-reliability-position.png`, fullPage: true });
    const TYPE = { pneumatic_cylinder: "Cilindro pneumático", electric_motor: "Motor elétrico", robot_axis: "Eixo de robô" };
    await page.getByRole("button", { name: "Tipo de componente", exact: true }).or(page.getByRole("tab", { name: "Tipo de componente", exact: true })).first().click();
    await page.waitForTimeout(2000);
    rows = await tableRows();
    for (const [key, exp] of Object.entries(X.reliability_by_component_type)) {
      const r = rows.find((t) => t.startsWith(TYPE[key]));
      check(r && r.includes(` ${exp.n} `) && r.includes(`${nf(exp.mtbf_h)} h`) && (exp.mcbf_cycles == null || r.includes(nf(exp.mcbf_cycles))) && r.includes(mttr(exp.mttr_min)) && r.includes(` ${exp.restored} `),
        `type ${key}: n=${exp.n} MTBF ${nf(exp.mtbf_h)} h MTTR ${mttr(exp.mttr_min)} restored ${exp.restored}`, r);
    }
    await page.getByRole("button", { name: "Fabricante", exact: true }).or(page.getByRole("tab", { name: "Fabricante", exact: true })).first().click();
    await page.waitForTimeout(2000);
    rows = await tableRows();
    for (const [key, exp] of Object.entries(X.reliability_by_manufacturer)) {
      const [type, man] = key.split("|");
      const r = rows.find((t) => t.startsWith(`${man} · ${TYPE[type]}`));
      check(r && r.includes(` ${exp.n} `) && r.includes(`${nf(exp.mtbf_h)} h`) && r.includes(nf(exp.mcbf_cycles)) && r.includes(mttr(exp.mttr_min)) && r.includes(` ${exp.restored} `),
        `manufacturer ${key}: n=${exp.n} MTBF ${nf(exp.mtbf_h)} h MCBF ${nf(exp.mcbf_cycles)} MTTR ${mttr(exp.mttr_min)}`, r);
    }
    await page.getByRole("button", { name: "Fabricante / modelo (MTTF)", exact: true }).or(page.getByRole("tab", { name: "Fabricante / modelo (MTTF)", exact: true })).first().click();
    await page.waitForTimeout(2000);
    rows = await tableRows();
    for (const [key, exp] of Object.entries(X.mttf_by_manufacturer_model)) {
      const r = rows.find((t) => t.startsWith(key.replace("|", " ")));
      const mttfTxt = exp.mttf == null ? "sem falhas" : `${nf(exp.mttf)} ${exp.measure === "cycles" ? "ciclos" : "h"}`;
      check(r && r.includes(mttfTxt) && r.includes(`n = ${exp.failures} falha${exp.failures === 1 ? "" : "s"}, ${exp.suspensions} suspens`) && r.trim().endsWith(String(exp.in_service)),
        `MTTF ${key}: ${mttfTxt}, ${exp.failures} falhas, ${exp.suspensions} suspensões, ${exp.in_service} em serviço`, r);
    }
    check(rows.find((t) => t.startsWith("WEG"))?.includes("amostra pequena"), "WEG flagged 'amostra pequena'");
    await page.screenshot({ path: `${OUT}/e6-reliability-mttf.png`, fullPage: true });
    check(errors.length === 0, "no console errors", errors);
    await context.close();
  }

  // ── (2) OS-103 reproduced on the phone (360 px) ───────────────────────────
  console.log("\n[2] component swap completed on mobile (OS-103 semantics)");
  {
    const status = (await api("GET", "/api/maintenance-plans/status", T.planner)).data;
    const pl1 = status.find((p) => p.plan_id === planId.PL1);
    const before = {};
    for (const pid of ["PL1", "PL2", "PL5", "PL6"]) before[pid] = (await api("GET", `/api/maintenance-plans/${planId[pid]}`, T.planner)).data.last_reset_at;
    const pos = (await api("GET", `/api/positions/${pl1.position_id}`, T.planner)).data;
    const oldComp = pos.installed_component;
    check(!!oldComp, `POS-A1 has an installed component (${oldComp?.serial_number})`);
    const oldBefore = (await api("GET", `/api/components/${oldComp.id}`, T.planner)).data;
    const created = await api("POST", "/api/work-orders/", T.planner, { type: "corrective", title: "QA: vazamento no Cilindro A1 — troca (reprodução OS-103)", estimated_hours: 1.5, position_id: pl1.position_id });
    let w = created.data;
    for (const to of ["approved", "scheduled"]) {
      if (w.state === "open" || to === "scheduled") {
        const r = await api("POST", `/api/work-orders/${w.id}/transition`, T.planner, { to, ...(to === "scheduled" ? { scheduled_for: "2026-12-01", assignee_id: (await api("GET", "/api/auth/me", T.tech)).data.id } : {}) });
        if (r.status !== 200) console.log(`    (transition ${to}: ${r.status} ${JSON.stringify(r.data)})`);
        else w = r.data;
      }
    }
    check(w.state === "scheduled", `WO ${w.number} scheduled for the technician today`, w.state);

    const { context, page, errors } = await newPage(browser, { width: 360, height: 740 }, true);
    await uiLogin(page, process.env.TECH);
    await page.waitForURL(/\/m\//, { timeout: 15_000 });
    await page.getByText(w.number).first().waitFor({ timeout: 15_000 });
    await page.screenshot({ path: `${OUT}/e2-mobile-list.png`, fullPage: true });
    await page.getByText(w.number).first().click();
    await page.getByRole("button", { name: /^Iniciar$/ }).click();
    await page.getByRole("button", { name: "Registrar troca" }).first().waitFor({ timeout: 15_000 });
    await page.getByRole("button", { name: "Registrar troca" }).first().click();
    const sheet = page.getByRole("dialog");
    await sheet.getByTestId("current-component").waitFor({ timeout: 10_000 });
    const curTxt = await sheet.getByTestId("current-component").innerText();
    check(curTxt.includes(oldComp.serial_number ?? "") && /340\.000 ciclos/.test(curTxt), `sheet shows the component leaving with its age: "${curTxt.replace(/\s+/g, " ")}"`);
    await sheet.getByLabel(/Falha/).first().check().catch(() => {});
    const serial = `QA-A1-${Date.now().toString().slice(-6)}`;
    await sheet.getByLabel("Série do componente novo").fill(serial);
    await sheet.getByLabel("Fabricante").fill("Festo").catch(() => {});
    await sheet.getByLabel("Modelo").fill("DSBC-50-100-PPVA-N3").catch(() => {});
    await page.screenshot({ path: `${OUT}/e2-mobile-replace-sheet.png`, fullPage: true });
    await sheet.getByRole("button", { name: "Registrar troca" }).click();
    await page.getByTestId("exec-notice").filter({ hasText: /Troca registrada/ }).waitFor({ timeout: 10_000 });
    await page.getByRole("button", { name: /^Concluir$/ }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Concluir OS" }).click();
    await page.getByTestId("exec-notice").filter({ hasText: /OS concluída/ }).waitFor({ timeout: 10_000 });
    await page.screenshot({ path: `${OUT}/e2-mobile-completed.png`, fullPage: true });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    check(!overflow, "no horizontal overflow at 360 px");
    check(errors.length === 0, "no console errors on the phone", errors);
    await context.close();

    const done = (await api("GET", `/api/work-orders/${w.id}`, T.planner)).data;
    check(done.state === "completed", `${done.number} completed`, done.state);
    const rep = done.replacements?.[0];
    check(rep && rep.removed_component_id === oldComp.id, "replacement recorded: old component removed", rep);
    const after = {};
    for (const pid of ["PL1", "PL2", "PL5", "PL6"]) after[pid] = (await api("GET", `/api/maintenance-plans/${planId[pid]}`, T.planner)).data.last_reset_at;
    check(after.PL1 !== before.PL1 && Date.parse(after.PL1) === AT.getTime(), `PL1 reset at completion (${before.PL1} → ${after.PL1})`);
    check(after.PL5 === before.PL5 && after.PL6 === before.PL6, `PL5/PL6 (machine plans) not reset`, { before, after });
    check(after.PL2 === before.PL2, `PL2 (other position) not reset`);
    const st = (await api("GET", "/api/maintenance-plans/status", T.planner)).data.find((p) => p.plan_id === planId.PL1);
    check(st.since_maintenance.cycles === 0 && st.consumed === 0, `PL1 since maintenance = 0 cycles, consumed 0`, st.since_maintenance);
    const oldC = (await api("GET", `/api/components/${oldComp.id}`, T.planner)).data;
    check(oldC.state === "removed" && oldC.position_id === null && oldC.removal_reason === "failure", `old component removed (failure)`, { state: oldC.state, reason: oldC.removal_reason });
    check(oldC.usage_since_install.cycles === oldBefore.usage_since_install.cycles && oldC.usage_since_install.cycles === 340000, `old component keeps its age at removal: ${oldC.usage_since_install.cycles} cycles`);
    const newC = (await api("GET", `/api/components/${rep.installed_component_id}`, T.planner)).data;
    check(newC.position_id === pl1.position_id && (newC.usage_since_install.cycles ?? 0) === 0 && (newC.usage_since_install.hours ?? 0) === 0, `new component ${newC.serial_number} installed with 0`, newC.usage_since_install);
    // history in the UI (planner): component history of the position shows both
    const ctx2 = await newPage(browser, { width: 1440, height: 1000 });
    await uiLogin(ctx2.page, process.env.PLANNER);
    await ctx2.page.goto(`${BASE}/maintenance/components?band=all`);
    await ctx2.page.waitForTimeout(3000);
    const ctxt = (await ctx2.page.locator("main").innerText()).replace(/\s+/g, " ");
    check(ctxt.includes(serial), `Componentes shows the new component ${serial}`);
    await ctx2.page.screenshot({ path: `${OUT}/e2-components.png`, fullPage: true });
    await ctx2.context.close();
  }
} finally {
  await browser.close();
}
console.log(failures ? `\n${failures} CHECK(S) FAILED` : "\nALL CHECKS PASSED");
process.exit(failures ? 1 : 0);
