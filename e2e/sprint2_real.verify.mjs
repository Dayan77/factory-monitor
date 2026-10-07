// Sprint 2 — browser checks against the REAL running stack fed by the virtual plant through the edge gateway
// (virtual plant -> gateway -> MQTT -> server -> WebSocket -> UI). Complements web/e2e/assets.verify.mjs,
// whose real mode covers the install/remove flow but not live data.
//
// Prerequisites: backend (:8000) + vite (:5173) running, dev seed + `python -m edge.src.provision` done, the
// virtual plant and the gateway publishing (speed >= 10 so counters move within seconds).
//
//   node e2e/sprint2_real.verify.mjs
//
// Env: E2E_BASE_URL (http://localhost:5173), E2E_API (http://localhost:8000), E2E_EMAIL, E2E_PASSWORD,
//      E2E_SHOTS_DIR (default e2e/screenshots), E2E_LEAD=1 to also post a real lead through the app's form.
import { existsSync, mkdirSync } from "node:fs";
import path from "node:path";

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:5173";
const API = process.env.E2E_API ?? "http://localhost:8000";
const EMAIL = process.env.E2E_EMAIL ?? "admin@factory-monitor.com";
const PASSWORD = process.env.E2E_PASSWORD ?? "Admin@1234";
const SHOTS = process.env.E2E_SHOTS_DIR ?? "e2e/screenshots";
const PW = existsSync("/opt/node-tools/node_modules/playwright/index.mjs") ? "/opt/node-tools/node_modules/playwright/index.mjs" : "playwright";
const { chromium } = await import(PW);
mkdirSync(SHOTS, { recursive: true });

let passed = 0;
const failed = [];
function check(ok, name, extra = "") {
  if (ok) { passed++; console.log(`  ✓ ${name}`); } else { failed.push(name); console.log(`  ✗ ${name} ${extra}`); }
}
const num = (s) => Number(String(s).replace(/[^\d,]/g, "").replace(",", "."));

const tok = (await (await fetch(`${API}/api/auth/login`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email: EMAIL, password: PASSWORD }) })).json()).access_token;
const api = async (p) => (await fetch(`${API}${p}`, { headers: { authorization: `Bearer ${tok}` } })).json();
const tree = await api("/api/plants/tree");
const plant = tree.find((p) => p.name === "Planta Manaus");
const montagem = plant.lines.find((l) => l.name === "Linha de Montagem");
const fixacao = montagem.machines.find((m) => m.name === "Estação de Fixação");

const browser = await chromium.launch({ executablePath: existsSync("/opt/pw-browsers/chromium") ? "/opt/pw-browsers/chromium" : undefined });
const VIEWPORTS = { desktop: { width: 1440, height: 900 }, mobile: { width: 390, height: 844 } };

for (const theme of ["light", "dark"]) {
  for (const [vpName, viewport] of Object.entries(VIEWPORTS)) {
    console.log(`\n[real] ${theme} / ${vpName}`);
    const context = await browser.newContext({ viewport, colorScheme: theme, isMobile: vpName === "mobile", hasTouch: vpName === "mobile" });
    const page = await context.newPage();
    const errors = [];
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    page.on("pageerror", (e) => errors.push(String(e)));
    const tag = `${theme}-${vpName}`;

    await page.goto(`${BASE}/login`);
    await page.getByLabel(/e-mail/i).fill(EMAIL);
    await page.getByLabel(/senha/i).fill(PASSWORD);
    await page.getByRole("button", { name: /entrar/i }).click();
    await page.waitForURL(/dashboard/);
    check((await page.evaluate(() => document.documentElement.dataset.theme)) === theme, `theme follows OS (${theme})`);

    // Fábrica -> Linha -> Máquina drill-down
    await page.goto(`${BASE}/factory/list`);   // Sprint 6: /factory is the plant map; the line cards moved to /factory/list
    await page.getByTestId("line-card").first().waitFor();
    // The dev stack may also hold the legacy simulator's "Linha Demo (Simulador)", which LineCreate puts in the
    // tenant's first plant (Planta Manaus) — so require both virtual-plant lines rather than exactly 2 cards.
    const cardTexts = await page.getByTestId("line-card").allInnerTexts();
    check(["Linha de Usinagem", "Linha de Montagem"].every((n) => cardTexts.some((t) => t.includes(n))),
      "Fábrica shows both virtual-plant lines of Planta Manaus", JSON.stringify(cardTexts.map((t) => t.split("\n")[0])));
    await page.screenshot({ path: path.join(SHOTS, `s2-real-factory-${tag}.png`), fullPage: true });
    await page.getByTestId("line-card").filter({ hasText: "Linha de Montagem" }).click();
    await page.waitForURL(/\/factory\/lines\//);
    await page.getByText(/Cadência média|Produção indisponível|Sem produção/).first().waitFor();
    await page.waitForTimeout(800);
    const bars = await page.locator(".recharts-bar-rectangle path, .recharts-rectangle").count();
    check(bars > 0, `line production chart has bars (${bars})`);
    const tableToggle = page.getByText(/Ver dados em tabela/).first();
    if (await tableToggle.count()) {
      await tableToggle.click();
      const rows = await page.locator("table tbody tr").count();
      check(rows > 0, `line chart data table has rows (${rows})`);
    } else check(false, "line chart offers 'Ver dados em tabela'");
    await page.screenshot({ path: path.join(SHOTS, `s2-real-line-${tag}.png`), fullPage: true });
    await page.getByTestId("machine-tile").filter({ hasText: "Estação de Fixação" }).click();
    await page.waitForURL(/\/factory\/machines\//);

    if (vpName === "desktop") {
      const row = page.getByRole("row", { name: "Abrir posição Cilindro A3" });
      await row.waitFor();
      // Sprint 4 added the machine's maintenance panel (plan consumption bars): count only the life bars
      const bars = page.getByRole("progressbar", { name: /^Vida consumida/ });
      // one life bar per position whose installed component has a nominal life (A1–A4 and, since Sprint 5, the
      // predictive scenario's Cilindro B3): derived from the API instead of a fixed count
      const usage = await api(`/api/machines/${fixacao.id}/usage`);
      const withLife = usage.positions.filter((p) => p.life_consumed_pct != null).length;
      check(withLife >= 4 && (await bars.count()) === withLife, `${withLife} life bars on Estação de Fixação (${await bars.count()})`);
      const a3 = usage.positions.find((p) => p.name === "Cilindro A3");
      const now = Number(await page.getByRole("progressbar", { name: "Vida consumida — Cilindro A3" }).getAttribute("aria-valuenow"));
      check(Math.abs(now - Math.round(a3.life_consumed_pct * 100)) <= 1, `A3 life bar ${now}% == API ${(a3.life_consumed_pct * 100).toFixed(2)}%`);
      const cell = row.locator("td").nth(1);
      const before = num(await cell.innerText());
      let after = before;
      const t0 = Date.now();
      while (after <= before && Date.now() - t0 < 45000) { await page.waitForTimeout(1000); after = num(await cell.innerText()); }
      check(after > before, `A3 cycles increase live without reload (${before} -> ${after})`);
      const apiNow = (await api(`/api/machines/${fixacao.id}/usage`)).positions.find((p) => p.name === "Cilindro A3").cycles_total;
      check(Math.abs(apiNow - after) <= 3, `UI live value ${after} tracks API ${apiNow}`);
    } else {
      await page.getByRole("button", { name: /Cilindro A3/ }).first().waitFor();
      check(true, "mobile position cards render");
    }
    await page.screenshot({ path: path.join(SHOTS, `s2-real-machine-${tag}.png`), fullPage: true });

    // position drawer
    if (vpName === "desktop") await page.getByRole("row", { name: "Abrir posição Cilindro A2" }).click();
    else await page.getByRole("button", { name: /Cilindro A2/ }).first().click();
    const dialog = page.getByRole("dialog");
    await dialog.getByText("Componente instalado").waitFor();
    await page.waitForTimeout(400);
    check(page.url().includes("pos="), "drawer state in URL");
    check(await dialog.getByText(/Fim de vida projetado|dias restantes|sem uso recente|insuficiente|sem vida nominal/i).first().count() > 0, "drawer shows projection or its reason");
    await page.screenshot({ path: path.join(SHOTS, `s2-real-drawer-${tag}.png`), fullPage: true });
    await page.keyboard.press("Escape");

    // app landing (served at app root)
    await page.goto(`${BASE}/`);
    await page.waitForLoadState("networkidle");
    const body = await page.locator("body").innerText();
    check(!/Metal[uú]rgica Fonseca|Pl[aá]sticos Norte|Alimentos BomDia|50\+ plantas|99[.,]9%/i.test(body), "app landing has no fake cases/figures");
    check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), "landing has no horizontal overflow");
    if (process.env.E2E_LEAD === "1" && theme === "dark" && vpName === "desktop") {
      const form = page.locator("form").filter({ has: page.getByRole("checkbox") }).last();
      await form.scrollIntoViewIfNeeded();
      await form.getByLabel(/nome/i).first().fill("QA E2E Lead");
      await form.getByLabel(/e-mail/i).first().fill("qa-e2e-lead@empresa.com.br");
      const msg = form.getByLabel(/mensagem/i);
      if (await msg.count()) await msg.first().fill("Teste automatizado de QA (Sprint 2).");
      await form.getByRole("checkbox").check();
      const [resp] = await Promise.all([
        page.waitForResponse((r) => r.url().includes("/api/public/leads")),
        form.getByRole("button", { name: /enviar/i }).click(),
      ]);
      check(resp.status() === 202, `real lead POST through vite proxy -> ${resp.status()}`);
      await page.getByText(/recebemos|obrigad|enviad/i).first().waitFor({ timeout: 5000 }).then(() => check(true, "lead success message"), () => check(false, "lead success message"));
    }
    await page.screenshot({ path: path.join(SHOTS, `s2-real-landing-${tag}.png`) });
    check(errors.length === 0, `zero console errors${errors.length ? ": " + errors.slice(0, 3).join(" | ") : ""}`);
    await context.close();
  }
}
await browser.close();
console.log(`\n${passed} passed, ${failed.length} failed`);
process.exit(failed.length ? 1 : 0);
