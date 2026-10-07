/**
 * Sprint 6 acceptance 4 + 8 (docs/sprint6.md §2, §6.4, §6.8) and reports §6 items 1/7 (docs/reports.md) on the dev
 * tenant (seed_dev + simulator + earlier virtual-plant runs: has data for "yesterday").
 *
 *   A. API, period=yesterday: Σ Pareto minutes of the lines = Pareto of the plant (per reason); plant OEE components
 *      = Σ of the lines' components and OEE = A×P×Q (never the mean of the lines)
 *   B. browser drill-down Fábrica → Linha → Máquina → Posição keeps period=yesterday (links, breadcrumbs, back)
 *      at 1366×768, 1280×800, 390×844
 *   C. reports: every catalog report generates PDF (+XLSX when listed) over HTTP; daily_production JSON headline
 *      numbers = /api/oee of the same plant/day; "Exportar PDF/Excel" on OEE, Paradas, Relatório de turno, Gestão
 *      da manutenção, Confiabilidade downloads a real PDF/XLSX; "Relatórios agendados": create, pause, resume, send
 *      now, history
 *   D. every new screen in light + dark at the 3 sizes: no console errors, no horizontal page overflow; screenshots
 *
 *   BASE_URL=http://localhost:5173 API=http://localhost:8000 node e2e/sprint6/drill_reports_screens.verify.mjs
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.BASE_URL ?? "http://localhost:5173";
const API = process.env.API ?? "http://localhost:8000";
const REPO = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../..");
const OUT = process.env.OUT ?? path.join(REPO, "e2e/screenshots/sprint6/screens");
fs.mkdirSync(OUT, { recursive: true });
let failures = 0;
const check = (c, m, got) => { console.log(`  ${c ? "✓" : "✗"} ${m}${c || got === undefined ? "" : `   got=${JSON.stringify(got).slice(0, 500)}`}`); if (!c) failures += 1; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function api(method, p, token, body) {
  const r = await fetch(`${API}${p}`, { method, headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  const ct = r.headers.get("content-type") ?? "";
  return { status: r.status, headers: r.headers, data: ct.includes("json") ? await r.json().catch(() => null) : Buffer.from(await r.arrayBuffer()) };
}
const tok = (await api("POST", "/api/auth/login", null, { email: "admin@factory-monitor.com", password: "Admin@1234" })).data.access_token;
const tree = (await api("GET", "/api/plants/tree", tok)).data;
const plant = tree[0];

// ── A. sums ─────────────────────────────────────────────────────────────────
{
  const pp = (await api("GET", `/api/losses/pareto?scope=plant&id=${plant.id}&period=yesterday`, tok)).data;
  const sum = {};
  for (const l of plant.lines) for (const r of (await api("GET", `/api/losses/pareto?scope=line&id=${l.id}&period=yesterday`, tok)).data) sum[r.reason_code ?? "∅"] = (sum[r.reason_code ?? "∅"] ?? 0) + r.duration_s;
  const plantBy = Object.fromEntries(pp.map((r) => [r.reason_code ?? "∅", r.duration_s]));
  const keys = new Set([...Object.keys(sum), ...Object.keys(plantBy)]);
  const diff = [...keys].filter((k) => Math.abs((sum[k] ?? 0) - (plantBy[k] ?? 0)) > 1e-6).map((k) => ({ k, lines: sum[k] ?? 0, plant: plantBy[k] ?? 0 }));
  check(pp.length > 0 && diff.length === 0, `yesterday: Σ Pareto of ${plant.lines.length} lines = plant Pareto (${pp.length} reasons, ${(pp.reduce((a, r) => a + r.duration_s, 0) / 60).toFixed(1)} min)`, diff);
  const po = (await api("GET", `/api/oee?scope=plant&id=${plant.id}&period=yesterday`, tok)).data;
  const lo = [];
  for (const l of plant.lines) lo.push((await api("GET", `/api/oee?scope=line&id=${l.id}&period=yesterday`, tok)).data);
  const comps = ["pot_s", "pbt_s", "apt_s", "adot_s", "pdot_s", "ideal_produced_s"];
  const cdiff = comps.filter((c) => Math.abs(lo.reduce((a, o) => a + (o[c] ?? 0), 0) - (po[c] ?? 0)) > 1e-3).map((c) => ({ c, lines: lo.reduce((a, o) => a + (o[c] ?? 0), 0), plant: po[c] }));
  check(cdiff.length === 0, "plant OEE components = Σ of the lines' components", cdiff);
  const apq = po.availability * po.performance * po.quality;
  check(po.oee != null && Math.abs(apq - po.oee) < 1e-9, `plant OEE ${po.oee?.toFixed(4)} = A×P×Q (${apq.toFixed(4)})`, po);
  const defined = lo.filter((o) => o.oee != null);
  const mean = defined.reduce((a, o) => a + o.oee, 0) / defined.length;
  console.log(`  lines OEE ${defined.map((o) => o.oee.toFixed(4)).join(", ")} → arithmetic mean ${mean.toFixed(4)} vs plant ${po.oee?.toFixed(4)} (period ${JSON.stringify(po.period)})`);
  check(defined.length < 2 || Math.abs(mean - po.oee) > 1e-6 || new Set(defined.map((o) => o.oee.toFixed(6))).size === 1, "plant OEE is not the mean of the line OEEs");
}

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
async function newPage(vp, scheme) {
  const ctx = await browser.newContext({ viewport: vp, locale: "pt-BR", timezoneId: "America/Manaus", colorScheme: scheme, acceptDownloads: true, ...(vp.width < 500 ? { isMobile: true, hasTouch: true } : {}) });
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
  return { ctx, page, errors };
}
const VPS = { desktop: { width: 1366, height: 768 }, tablet: { width: 1280, height: 800 }, phone: { width: 390, height: 844 } };

// ── B. drill-down ───────────────────────────────────────────────────────────
for (const [name, vp] of Object.entries(VPS)) {
  const { ctx, page, errors } = await newPage(vp, name === "phone" ? "dark" : "light");
  await page.goto(`${BASE}/factory/list?period=yesterday`);
  const lineLink = page.locator('a[href^="/factory/lines/"]').first();
  await lineLink.waitFor({ timeout: 15000 });
  const lh = await lineLink.getAttribute("href");
  await lineLink.click();
  await page.waitForURL(/\/factory\/lines\//);
  const u1 = page.url();
  const mt = page.getByTestId("machine-tile").first();
  await mt.waitFor({ timeout: 15000 });
  const mh = await mt.getAttribute("href");
  await mt.click();
  await page.waitForURL(/\/factory\/machines\//);
  const u2 = page.url();
  const pr = page.locator('[data-testid="position-row"]:visible, ul.md\\:hidden > li > button:visible').first();
  await pr.waitFor({ timeout: 15000 }).catch(() => {});
  let u3 = null;
  if (await pr.count()) { await pr.click(); await sleep(1200); u3 = page.url(); }
  const crumbs = await page.locator('nav[aria-label="Navegação estrutural"]').first().innerText().catch(() => "");
  const crumbLinks = await page.locator('nav[aria-label="Navegação estrutural"] a').evaluateAll((els) => els.map((e) => e.getAttribute("href")));
  await page.screenshot({ path: path.join(OUT, `drill_${name}.png`) });
  await page.goBack(); await sleep(800);
  const back1 = page.url();
  const all = [lh, u1, mh, u2, u3, ...crumbLinks].filter(Boolean);
  check(all.every((x) => /period=yesterday/.test(x)) && u3 !== null, `[${name}] Fábrica → Linha → Máquina → Posição keeps period=yesterday (links, URLs, breadcrumbs)`, { all, crumbs });
  check(/period=yesterday/.test(back1), `[${name}] browser back keeps period`, back1);
  console.log(`    breadcrumbs: ${crumbs.replace(/\s+/g, " ")}`);
  check(errors.length === 0, `[${name}] drill without console errors`, errors.slice(0, 3));
  await ctx.close();
}

// ── C. reports ──────────────────────────────────────────────────────────────
const catalog = (await api("GET", "/api/reports/catalog", tok)).data;
const yday = (await api("GET", `/api/oee?scope=plant&id=${plant.id}&period=yesterday`, tok)).data;
const pday = yday.period?.productive_day;
const line0 = plant.lines.find((l) => l.name === "Linha de Montagem") ?? plant.lines[0];
const scopeFor = (c) => (c.scope?.includes("plant") ? ["plant", plant.id] : ["line", line0.id]);
const paramsFor = (code) => ({ daily_production: { productive_day: pday }, shift: {}, weekly_oee: {}, monthly_maintenance: {}, reliability_life: {} })[code] ?? {};
for (const c of catalog) {
  for (const f of c.formats) {
    const r = await api("POST", "/api/reports/generate", tok, { code: c.code, scope_type: scopeFor(c)[0], scope_id: scopeFor(c)[1], format: f, params: paramsFor(c.code), mode: "file" });
    const buf = Buffer.isBuffer(r.data) ? r.data : Buffer.from("");
    const magic = f === "pdf" ? buf.subarray(0, 4).toString() === "%PDF" : buf.subarray(0, 2).toString() === "PK";
    check(r.status === 200 && magic, `report ${c.code} ${f}: ${r.status}, ${buf.length} bytes, v${r.headers.get("x-report-version")}`, r.status === 200 ? buf.subarray(0, 8).toString() : r.data);
  }
}
const dj = await api("POST", "/api/reports/generate", tok, { code: "daily_production", scope_type: "plant", scope_id: plant.id, format: "json", params: { productive_day: pday } });
const calls = dj.data?.api_calls ?? [];
const oeeCall = calls.find((x) => /\/api\/oee/.test(x.path ?? x.url ?? "") && /scope=plant/.test(x.path ?? x.url ?? ""));
console.log(`  daily_production ${pday}: headline ${JSON.stringify(dj.data?.headline ?? dj.data?.summary)?.slice(0, 300)}`);
const flat = JSON.stringify(dj.data).replace(/[\u00a0\u202f]/g, " ");
const pct = (v) => (v * 100).toFixed(1).replace(".", ",") + " %";
check(dj.status === 200 && yday.oee != null && flat.includes(pct(yday.oee)), `daily_production shows the plant OEE of /api/oee (${pct(yday.oee ?? NaN)})`, { status: dj.status, oeeCall });

// UI exports + scheduled reports
{
  const { ctx, page, errors } = await newPage(VPS.desktop, "light");
  const screens = [["/oee", "OEE"], ["/stops", "Paradas"], ["/reports/shift", "Relatório de turno"], ["/maintenance/kpis", "Gestão da manutenção"], ["/maintenance/reliability", "Confiabilidade"]];
  for (const [p, label] of screens) {
    await page.goto(`${BASE}${p}`);
    const btn = page.locator('[data-testid^="export-"][aria-haspopup="menu"]').first();
    const has = await btn.waitFor({ timeout: 15000 }).then(() => true).catch(() => false);
    if (!has) { check(false, `${label}: export button present`); continue; }
    const code = (await btn.getAttribute("data-testid")).replace("export-", "");
    const got = [];
    for (const f of ["pdf", "xlsx"]) {
      await btn.click();
      const item = page.getByTestId(`export-${code}-${f}`);
      if (!(await item.count())) { await page.keyboard.press("Escape"); continue; }
      const obs = page.locator('textarea, input[name="observations"]');
      void obs;
      const [dl] = await Promise.all([page.waitForEvent("download", { timeout: 60000 }).catch(() => null), item.click()]);
      if (!dl) { got.push(`${f}:no download`); continue; }
      const fp = path.join(OUT, `export_${code}.${f}`); await dl.saveAs(fp);
      const b = fs.readFileSync(fp);
      got.push(`${f}:${(f === "pdf" ? b.subarray(0, 4).toString() === "%PDF" : b.subarray(0, 2).toString() === "PK") ? "ok" : "bad"}:${b.length}`);
    }
    check(got.length > 0 && got.every((g) => /:ok:/.test(g)), `${label}: Exportar (${code}) → ${got.join(", ")}`, got);
  }
  // scheduled reports
  await page.goto(`${BASE}/reports/scheduled`);
  await page.getByTestId("new-schedule").first().click();
  await page.selectOption("#sc-type", "daily_production");
  await page.fill("#sc-name", "QA diário");
  const scope = page.locator("#sc-scope");
  if (await scope.count()) await scope.selectOption("plant").catch(() => {});
  const sid = page.getByTestId("sc-scope-id");
  if (await sid.count()) await sid.selectOption(plant.id).catch(() => {});
  await page.getByTestId("save-schedule").click();
  const card = page.getByTestId("schedule-card").filter({ hasText: "QA diário" }).first();
  const created = await card.waitFor({ timeout: 15000 }).then(() => true).catch(() => false);
  check(created, "scheduled report created in the UI");
  if (created) {
    await card.getByTestId("pause").click();
    check(await card.getByTestId("resume").waitFor({ timeout: 10000 }).then(() => true).catch(() => false), "paused");
    await card.getByTestId("resume").click();
    check(await card.getByTestId("pause").waitFor({ timeout: 10000 }).then(() => true).catch(() => false), "resumed");
    await card.getByTestId("send-now").click();
    await sleep(6000);
    const sched = (await api("GET", "/api/report-schedules/", tok)).data.find((s) => (s.name ?? "") === "QA diário");
    const hist = sched ? (await api("GET", `/api/report-schedules/${sched.id}/history`, tok)).data : null;
    check(!!hist && hist.runs.length >= 1, `send now → ${hist?.runs.length ?? 0} run(s), ${hist?.dispatches.length ?? 0} dispatch(es), ${hist?.deliveries.length ?? 0} deliveries; warning="${sched?.warning ?? ""}"`, hist);
    await card.getByTestId("history").click();
    const hs = await page.getByTestId("history-sheet").waitFor({ timeout: 10000 }).then(() => true).catch(() => false);
    check(hs, "history sheet opens (versions / periods / deliveries)");
    await page.screenshot({ path: path.join(OUT, "scheduled_history.png") });
    await page.keyboard.press("Escape");
    if (sched) await api("DELETE", `/api/report-schedules/${sched.id}`, tok);
  }
  const mw = await page.getByTestId("mail-warning").isVisible().catch(() => false);
  console.log(`  mail-warning visible on /reports/scheduled: ${mw}`);
  check(errors.length === 0, "exports + schedules without console errors", errors.slice(0, 4));
  await ctx.close();
}

// ── D. new screens × themes × sizes ─────────────────────────────────────────
const line = plant.lines.find((l) => l.machines.length) ?? plant.lines[0];
const SCREENS = ["/factory", "/factory/list?period=yesterday", `/factory/lines/${line.id}?period=yesterday`, `/factory/machines/${line.machines[0].id}?period=yesterday`,
  "/factory/layout/edit", `/tv/plants/${plant.id}`, "/onboarding/1", "/onboarding/6", "/onboarding/9", "/reports/scheduled", "/oee", "/stops", "/reports/shift",
  "/maintenance/kpis", "/maintenance/reliability", "/op/login"];
const bad = [];
let n = 0;
for (const [name, vp] of Object.entries(VPS)) for (const scheme of ["light", "dark"]) {
  const { ctx, page, errors } = await newPage(vp, scheme);
  for (const s of SCREENS) {
    const e0 = errors.length;
    await page.goto(`${BASE}${s}`);
    await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
    await sleep(500);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    const errs = errors.slice(e0).filter((e) => !/vibrate/.test(e));
    n += 1;
    if (overflow || errs.length) bad.push({ s, vp: `${name}/${scheme}`, overflow, errs: errs.slice(0, 2) });
    await page.screenshot({ path: path.join(OUT, `${name}_${scheme}${s.split("?")[0].replace(/\//g, "_").replace(/[0-9a-f-]{36}/g, "id")}.png`) });
  }
  await ctx.close();
}
check(bad.length === 0, `${n} screen × size × theme checks: no console errors, no horizontal overflow`, bad);
await browser.close();
console.log(`\n${failures === 0 ? "OK" : "FAIL"} (${failures} failure(s))`);
process.exit(failures === 0 ? 0 : 1);
