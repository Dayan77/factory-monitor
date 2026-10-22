// Seção "Documentação" e páginas de segurança/LGPD no navegador real (Playwright + Chromium), sobre o build em dist/:
//  - axe-core (WCAG 2.1 A/AA) sem violação séria/crítica em todas as páginas do manual, /seguranca e /privacidade,
//    temas claro e escuro, desktop e celular; sem rolagem horizontal; zero erros de console
//  - todo link interno do manual abre (200) e toda âncora (#…) existe na página de destino
//  - índice "Nesta página" leva à seção; busca encontra "MTBF" e "justificar" e o resultado abre a seção certa
// Uso: npm run build && node tests/docs.verify.mjs   (BASE_URL=http://host:porta para outro servidor)
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { serve } from "./serve.mjs";

const OPT_PW = "/opt/node-tools/node_modules/playwright/index.mjs";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || (existsSync(OPT_PW) ? OPT_PW : "playwright"));
const OPT_CHROMIUM = "/opt/pw-browsers/chromium";
const executablePath = process.env.CHROMIUM_PATH || (existsSync(OPT_CHROMIUM) ? OPT_CHROMIUM : undefined);
const AXE = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
const root = fileURLToPath(new URL("../dist", import.meta.url));
const shots = fileURLToPath(new URL("../test-results/screens/docs", import.meta.url));
mkdirSync(shots, { recursive: true });

const PORT = Number(process.env.E2E_PORT || 5203);
let server;
let BASE = process.env.BASE_URL;
if (!BASE) { server = await serve(root, PORT); BASE = `http://127.0.0.1:${PORT}`; }

const PAGES = ["/docs", "/docs/indicadores", "/docs/operador", "/docs/supervisor", "/docs/planejador", "/docs/tecnico",
  "/docs/gerente", "/docs/administrador", "/seguranca", "/privacidade"];
const VIEWPORTS = { desktop: { viewport: { width: 1366, height: 900 } }, mobile: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 } };

let failures = 0;
const check = (c, m, got) => { console.log(`  ${c ? "✓" : "✗"} ${m}${c || got === undefined ? "" : `   got=${JSON.stringify(got).slice(0, 500)}`}`); if (!c) failures += 1; };
const browser = await chromium.launch({ executablePath });

for (const [vp, opts] of Object.entries(VIEWPORTS)) {
  for (const theme of ["dark", "light"]) {
    console.log(`\n[${vp} · ${theme}]`);
    const ctx = await browser.newContext({ ...opts, locale: "pt-BR", reducedMotion: "reduce" });
    await ctx.addInitScript((t) => { try { localStorage.setItem("fc-theme", t); } catch { /* */ } }, theme);
    const page = await ctx.newPage();
    const errors = [];
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    page.on("pageerror", (e) => errors.push(String(e)));
    for (const path of PAGES) {
      errors.length = 0;
      const res = await page.goto(BASE + path, { waitUntil: "networkidle" });
      await page.evaluate(() => document.querySelectorAll(".reveal").forEach((e) => e.classList.add("is-visible")));
      await page.addScriptTag({ content: AXE });
      const bad = await page.evaluate(async () => {
        // eslint-disable-next-line no-undef
        const r = await axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"] } });
        return r.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);
      });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      check(res.status() === 200, `${path} 200`);
      check(bad.length === 0, `${path} — axe sem violação séria/crítica`, bad);
      check(overflow <= 0, `${path} — sem rolagem horizontal`, overflow);
      check(errors.length === 0, `${path} — sem erros de console`, errors);
      await page.screenshot({ path: `${shots}/${vp}-${theme}-${path.slice(1).replace(/\//g, "_")}.png`, fullPage: path === "/docs" });
    }
    await ctx.close();
  }
}

console.log("\n[links e âncoras do manual]");
{
  const page = await browser.newPage();
  const targets = new Set();
  for (const path of PAGES.filter((p) => p.startsWith("/docs"))) {
    await page.goto(BASE + path, { waitUntil: "domcontentloaded" });
    for (const href of await page.$$eval(".prose-doc a[href^='/'], nav a[href^='/docs'], nav a[href^='#']", (as) => as.map((a) => a.getAttribute("href")))) {
      targets.add(href.startsWith("#") ? `${path}${href}` : href);
    }
  }
  const broken = [];
  for (const t of targets) {
    const [p, hash] = t.split("#");
    const r = await page.goto(BASE + p, { waitUntil: "domcontentloaded" });
    if (r.status() !== 200) { broken.push(`${t} → ${r.status()}`); continue; }
    if (hash && !(await page.evaluate((h) => !!document.getElementById(decodeURIComponent(h)), hash))) broken.push(`${t} (âncora)`);
  }
  check(broken.length === 0, `${targets.size} links internos abrem e as âncoras existem`, broken);
  await page.close();
}

console.log("\n[busca e índice]");
{
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 }, locale: "pt-BR" });
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  await page.goto(`${BASE}/docs/indicadores`, { waitUntil: "networkidle" });
  const toc = page.locator('nav[aria-label="Nesta página"] a').filter({ hasText: "MTBF" }).first();
  check(await toc.count() === 1, "índice 'Nesta página' lista a seção MTBF");
  await toc.click();
  await page.waitForTimeout(900); // smooth scroll
  const hash = await page.evaluate(() => decodeURIComponent(location.hash.slice(1)));
  check(hash.startsWith("mtbf") && await page.evaluate((h) => { const r = document.getElementById(h)?.getBoundingClientRect(); return !!r && r.top >= 0 && r.top < 300; }, hash), "clicar no índice rola até a seção", hash);

  const box = page.locator("#docs-search");
  await box.click();
  await box.fill("justificar parada");
  const first = page.locator("[data-results] a").first();
  await first.waitFor({ timeout: 5000 });
  const href = await first.getAttribute("href");
  check(/\/docs\/(operador|supervisor)/.test(href ?? ""), "busca 'justificar parada' → manual do operador/supervisor", href);
  await box.fill("mtbf");
  await page.waitForTimeout(200);
  const hrefs = await page.locator("[data-results] a").evaluateAll((as) => as.map((a) => a.getAttribute("href")));
  check(hrefs.includes("/docs/indicadores#mtbf"), "busca 'mtbf' encontra a definição (/docs/indicadores#mtbf)", hrefs);
  await page.locator('[data-results] a[href="/docs/indicadores#mtbf"]').click();
  await page.waitForURL(/#mtbf$/);
  await page.waitForTimeout(900);
  check(await page.evaluate(() => { const r = document.getElementById("mtbf")?.getBoundingClientRect(); return !!r && r.top >= -10 && r.top < 300; }), "resultado abre a seção MTBF");
  await box.fill("zzzz-nada");
  await page.waitForTimeout(200);
  check((await page.locator("[data-results]").innerText()).includes("Nada encontrado"), "busca sem resultado avisa");
  check(errors.length === 0, "busca sem erros de console", errors);
  await page.screenshot({ path: `${shots}/busca.png` });
  await ctx.close();
}

await browser.close();
server?.close();
console.log(failures === 0 ? "\nOK — documentação verificada" : `\nFALHOU — ${failures} verificação(ões)`);
process.exit(failures ? 1 : 0);
