// Verificação real no navegador (Playwright + Chromium) do site estático em dist/.
// Uso: npm run build && node tests/e2e.mjs     (BASE_URL=http://host:porta para testar outro servidor, ex. o container nginx)
// Screenshots desktop + mobile (tema escuro e claro) em test-results/screens/.
import { mkdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import { serve } from "./serve.mjs";

import { existsSync } from "node:fs";
// Playwright: PLAYWRIGHT_MODULE > instalação do container de dev (/opt) > devDependency do projeto (CI).
const OPT_PW = "/opt/node-tools/node_modules/playwright/index.mjs";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || (existsSync(OPT_PW) ? OPT_PW : "playwright"));
const OPT_CHROMIUM = "/opt/pw-browsers/chromium";
const executablePath = process.env.CHROMIUM_PATH || (existsSync(OPT_CHROMIUM) ? OPT_CHROMIUM : undefined);
const root = fileURLToPath(new URL("../dist", import.meta.url));
const shots = fileURLToPath(new URL("../test-results/screens", import.meta.url));
mkdirSync(shots, { recursive: true });

const PORT = Number(process.env.E2E_PORT || 5202);
let server;
let BASE = process.env.BASE_URL;
if (!BASE) { server = await serve(root, PORT); BASE = `http://127.0.0.1:${PORT}`; }
const baseHost = new URL(BASE).host;

const PAGES = [
  ["/", "início"], ["/solucao", "solução"], ["/para-manutencao", "manutenção"], ["/para-producao", "produção"],
  ["/como-funciona", "como funciona"], ["/seguranca", "segurança"], ["/planos", "planos"], ["/sobre", "sobre"],
  ["/contato", "contato"], ["/privacidade", "privacidade"],
  ["/docs", "documentação"], ["/docs/indicadores", "manual: indicadores"], ["/docs/operador", "manual: operador"],
  ["/docs/supervisor", "manual: supervisor"], ["/docs/planejador", "manual: planejador"], ["/docs/tecnico", "manual: técnico"],
  ["/docs/gerente", "manual: gerente"], ["/docs/administrador", "manual: administrador"],
];
const VIEWPORTS = { desktop: { width: 1366, height: 900 }, mobile: { width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 2 } };
const FORBIDDEN = [/Metal[uú]rgica Fonseca/i, /Pl[aá]sticos Norte/i, /Alimentos BomDia/i, /depoimento/i, /Resultados reais/i, /R\$\s?\d/];

let passed = 0;
const failures = [];
async function check(name, fn) {
  try { await fn(); passed++; console.log(`  ✓ ${name}`); }
  catch (e) { failures.push(name); console.log(`  ✗ ${name}\n    ${e?.message?.split("\n").slice(0, 6).join("\n    ")}`); }
}

const browser = await chromium.launch({ executablePath });

async function newPage(vp) {
  const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, isMobile: vp.isMobile, hasTouch: vp.hasTouch, deviceScaleFactor: vp.deviceScaleFactor ?? 1, locale: "pt-BR", reducedMotion: "reduce" });
  const page = await ctx.newPage();
  const errors = [];
  const external = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("request", (r) => { const u = new URL(r.url()); if (!["data:", "about:"].includes(u.protocol) && u.host !== baseHost) external.push(r.url()); });
  return { ctx, page, errors, external };
}

console.log(`Site: ${BASE}`);

// ── Páginas: SEO, estrutura, console, overflow, screenshots ───────────────────
for (const [vpName, vp] of Object.entries(VIEWPORTS)) {
  console.log(`\n[${vpName}]`);
  const { ctx, page, errors, external } = await newPage(vp);
  for (const [path, label] of PAGES) {
    await check(`${vpName} ${path} (${label})`, async () => {
      errors.length = 0;
      const res = await page.goto(BASE + path, { waitUntil: "networkidle" });
      assert.equal(res.status(), 200, "status");
      const title = await page.title();
      assert.match(title, /FactoryConnect/, "title");
      const meta = await page.evaluate(() => ({
        desc: document.querySelector('meta[name="description"]')?.getAttribute("content") ?? "",
        canonical: document.querySelector('link[rel="canonical"]')?.getAttribute("href") ?? "",
        ogTitle: document.querySelector('meta[property="og:title"]')?.getAttribute("content") ?? "",
        ogImage: document.querySelector('meta[property="og:image"]')?.getAttribute("content") ?? "",
        ogUrl: document.querySelector('meta[property="og:url"]')?.getAttribute("content") ?? "",
        lang: document.documentElement.lang,
        h1: document.querySelectorAll("h1").length,
        ld: [...document.querySelectorAll('script[type="application/ld+json"]')].map((s) => JSON.parse(s.textContent || "{}")["@type"]),
        overflow: document.documentElement.scrollWidth - window.innerWidth,
        imgsNoAlt: [...document.querySelectorAll("img:not([alt])")].length,
        svgImgNoTitle: [...document.querySelectorAll('svg[role="img"]')].filter((s) => !s.querySelector("title")).length,
        text: document.body.innerText,
      }));
      assert.ok(meta.desc.length >= 70 && meta.desc.length <= 200, `description length ${meta.desc.length}`);
      assert.equal(meta.canonical, `https://factoryconnect.tech${path}`, "canonical");
      assert.equal(meta.ogUrl, meta.canonical, "og:url");
      assert.equal(meta.ogImage, "https://factoryconnect.tech/og.png", "og:image");
      assert.ok(meta.ogTitle, "og:title");
      assert.equal(meta.lang, "pt-BR");
      assert.equal(meta.h1, 1, "exactly one h1");
      assert.ok(meta.ld.includes("Organization"), "Organization JSON-LD");
      if (path === "/") assert.ok(meta.ld.includes("SoftwareApplication"), "SoftwareApplication JSON-LD");
      assert.ok(meta.overflow <= 0, `horizontal overflow ${meta.overflow}px`);
      assert.equal(meta.imgsNoAlt, 0, "img without alt");
      assert.equal(meta.svgImgNoTitle, 0, "svg role=img without <title>");
      for (const re of FORBIDDEN) assert.ok(!re.test(meta.text), `forbidden content ${re}`);
      // Header: Entrar e Teste grátis apontam para o app
      const login = await page.$$eval('a', (as) => as.filter((a) => a.textContent?.trim() === "Entrar").map((a) => a.getAttribute("href")));
      const reg = await page.$$eval('a', (as) => as.filter((a) => a.textContent?.trim() === "Teste grátis").map((a) => a.getAttribute("href")));
      assert.ok(login.length && login.every((h) => h === "https://app.factoryconnect.tech/login"), `Entrar ${login}`);
      assert.ok(reg.length && reg.every((h) => h === "https://app.factoryconnect.tech/register"), `Teste grátis ${reg}`);
      // Força visibilidade dos elementos com animação de entrada antes do screenshot de página inteira
      await page.evaluate(() => document.querySelectorAll(".reveal").forEach((e) => e.classList.add("is-visible")));
      const name = path === "/" ? "inicio" : path.slice(1);
      await page.screenshot({ path: `${shots}/${vpName}-${name}.png`, fullPage: true });
      assert.deepEqual(errors, [], "console errors");
    });
  }

  await check(`${vpName} 404`, async () => {
    const res = await page.goto(BASE + "/rota-que-nao-existe", { waitUntil: "networkidle" });
    assert.equal(res.status(), 404);
    assert.match(await page.title(), /não encontrada/);
    assert.equal(await page.getAttribute('meta[name="robots"]', "content"), "noindex, follow");
    await page.screenshot({ path: `${shots}/${vpName}-404.png`, fullPage: true });
  });

  await check(`${vpName} no third-party requests`, async () => {
    assert.deepEqual(external, []);
  });
  await ctx.close();
}

// ── Tema claro/escuro ───────────────────────────────────────────────────────
console.log("\n[interações]");
await check("theme toggle persists (dark → light → reload)", async () => {
  const { ctx, page, errors } = await newPage(VIEWPORTS.desktop);
  await page.goto(BASE + "/", { waitUntil: "networkidle" });
  assert.equal(await page.evaluate(() => document.documentElement.classList.contains("dark")), true, "dark by default");
  await page.click("[data-theme-toggle]");
  assert.equal(await page.evaluate(() => document.documentElement.classList.contains("dark")), false);
  assert.equal(await page.getAttribute("[data-theme-toggle]", "aria-pressed"), "true");
  await page.reload({ waitUntil: "networkidle" });
  assert.equal(await page.evaluate(() => document.documentElement.classList.contains("dark")), false, "light persisted");
  await page.evaluate(() => document.querySelectorAll(".reveal").forEach((e) => e.classList.add("is-visible")));
  await page.screenshot({ path: `${shots}/desktop-inicio-claro.png`, fullPage: true });
  await page.goto(BASE + "/contato", { waitUntil: "networkidle" });
  await page.screenshot({ path: `${shots}/desktop-contato-claro.png`, fullPage: true });
  assert.deepEqual(errors, []);
  await ctx.close();
});

await check("mobile menu opens, links work, Escape closes", async () => {
  const { ctx, page } = await newPage(VIEWPORTS.mobile);
  await page.goto(BASE + "/", { waitUntil: "networkidle" });
  assert.equal(await page.isVisible("#menu-mobile"), false);
  await page.click("[data-menu-toggle]");
  assert.equal(await page.getAttribute("[data-menu-toggle]", "aria-expanded"), "true");
  assert.equal(await page.isVisible("#menu-mobile >> text=Como funciona"), true);
  await page.screenshot({ path: `${shots}/mobile-menu-aberto.png` });
  await page.keyboard.press("Escape");
  assert.equal(await page.isVisible("#menu-mobile"), false);
  await page.click("[data-menu-toggle]");
  await page.click("#menu-mobile >> text=Planos");
  await page.waitForURL(/\/planos$/);
  await ctx.close();
});

await check("keyboard: skip link is the first focusable element", async () => {
  const { ctx, page } = await newPage(VIEWPORTS.desktop);
  await page.goto(BASE + "/solucao", { waitUntil: "networkidle" });
  await page.keyboard.press("Tab");
  assert.equal(await page.evaluate(() => document.activeElement?.textContent?.trim()), "Pular para o conteúdo");
  await ctx.close();
});

// ── Formulário de contato ───────────────────────────────────────────────────
async function contactPage(vp = VIEWPORTS.desktop) {
  const r = await newPage(vp);
  const requests = [];
  let reply = { status: 202, body: '{"status":"accepted"}' };
  await r.page.route("**/api/public/leads", async (route) => {
    const req = route.request();
    if (req.method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
    requests.push({ url: req.url(), method: req.method(), body: req.postDataJSON(), headers: req.headers() });
    await route.fulfill({ status: reply.status, body: reply.body, headers: { ...cors, "Content-Type": "application/json" } });
  });
  await r.page.goto(BASE + "/contato", { waitUntil: "networkidle" });
  return { ...r, requests, setReply: (s, b = "{}") => { reply = { status: s, body: b }; } };
}
const cors = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "content-type", "Access-Control-Allow-Methods": "POST" };
async function fillValid(page) {
  await page.fill("#f-name", "Ana Souza");
  await page.fill("#f-email", "ana@empresa.com.br");
  await page.fill("#f-company", "Empresa X");
  await page.fill("#f-phone", "(11) 91234-5678");
  await page.fill("#f-message", "Temos 6 injetoras e queremos medir paradas.");
  await page.check("#f-consent");
}

await check("form: empty submit shows errors, focuses first field, sends nothing", async () => {
  const { ctx, page, requests } = await contactPage(VIEWPORTS.mobile);
  await page.click("[data-submit]");
  for (const f of ["name", "email", "message", "consent"]) {
    assert.equal(await page.isVisible(`[data-error-for="${f}"]`), true, `error for ${f}`);
  }
  assert.equal(await page.getAttribute("#f-name", "aria-invalid"), "true");
  assert.equal(await page.evaluate(() => document.activeElement?.id), "f-name");
  assert.equal(requests.length, 0);
  await page.screenshot({ path: `${shots}/mobile-contato-erros.png`, fullPage: true });
  // corrigir o campo remove o erro ao sair dele
  await page.fill("#f-name", "Ana");
  await page.press("#f-name", "Tab");
  assert.equal(await page.getAttribute("#f-name", "aria-invalid"), "false");
  await ctx.close();
});

await check("form: honeypot is hidden and not tabbable", async () => {
  const { ctx, page } = await contactPage();
  const box = await page.locator("#f-website").boundingBox();
  assert.ok(!box || box.x < -1000, "honeypot off-screen");
  assert.equal(await page.getAttribute("#f-website", "tabindex"), "-1");
  await ctx.close();
});

await check("form: valid submit posts the API contract and shows success", async () => {
  const { ctx, page, requests, errors } = await contactPage();
  await fillValid(page);
  await page.click("[data-submit]");
  await page.waitForSelector("#form-success:not([hidden])");
  assert.equal(requests.length, 1);
  const [r] = requests;
  assert.equal(r.url, "https://app.factoryconnect.tech/api/public/leads");
  assert.equal(r.method, "POST");
  assert.match(r.headers["content-type"], /application\/json/);
  assert.deepEqual(r.body, {
    name: "Ana Souza", email: "ana@empresa.com.br", company: "Empresa X", phone: "(11) 91234-5678",
    message: "Temos 6 injetoras e queremos medir paradas.", consent: true, website: "", source: "/contato",
  });
  assert.equal(await page.isVisible("#lead-form"), false);
  assert.equal(await page.evaluate(() => document.activeElement?.id), "form-success");
  await page.screenshot({ path: `${shots}/desktop-contato-sucesso.png` });
  assert.deepEqual(errors, []);
  await ctx.close();
});

for (const [status, re] of [[429, /várias mensagens/], [500, /Não foi possível enviar/], [422, /Revise os campos/]]) {
  await check(`form: HTTP ${status} shows error and keeps the data`, async () => {
    const { ctx, page, setReply } = await contactPage();
    setReply(status, '{"detail":"x"}');
    await fillValid(page);
    await page.click("[data-submit]");
    await page.waitForSelector("#form-error:not(.hidden)");
    assert.match(await page.textContent("#form-error"), re);
    assert.equal(await page.inputValue("#f-email"), "ana@empresa.com.br");
    assert.equal(await page.isEnabled("[data-submit]"), true);
    if (status === 500) await page.screenshot({ path: `${shots}/desktop-contato-erro-servidor.png` });
    await ctx.close();
  });
}

await check("form: network failure shows connection error", async () => {
  const { ctx, page } = await newPage(VIEWPORTS.desktop);
  await page.route("**/api/public/leads", (route) => route.abort("internetdisconnected"));
  await page.goto(BASE + "/contato", { waitUntil: "networkidle" });
  await fillValid(page);
  await page.click("[data-submit]");
  await page.waitForSelector("#form-error:not(.hidden)");
  assert.match(await page.textContent("#form-error"), /Falha de conexão/);
  await ctx.close();
});

// ── "Ver demonstração" (docs/sprint6.md §5) ─────────────────────────────────
// O site só consulta GET /api/public/demo-status e navega para {APP}/demo; o app abre a sessão (POST) e guarda o
// token na própria origem — nenhum token passa pelo site nem aparece em URL.
console.log("\n[demonstração]");
const APP = "https://app.factoryconnect.tech";
async function demoPage(vp, path, { status = 200, body = { available: true, plant_running: true }, delayMs = 0, abort = false } = {}) {
  const r = await newPage(vp);
  const calls = { status: 0, session: 0, beacons: 0, app: [] };
  let reply = { status, body, delayMs, abort };
  await r.page.route("**/api/public/demo-status", async (route) => {
    calls.status++;
    if (reply.delayMs) await new Promise((res) => setTimeout(res, reply.delayMs));
    if (reply.abort) return route.abort("connectionrefused");
    await route.fulfill({ status: reply.status, body: JSON.stringify(reply.body),
                          headers: { "Access-Control-Allow-Origin": "*", "Content-Type": "application/json" } });
  });
  await r.page.route("**/api/public/demo-session", (route) => { calls.session++; return route.abort(); });
  await r.ctx.route(`${APP}/demo**`, (route) => {
    calls.app.push(route.request().url());
    return route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>app demo</title><h1>app /demo</h1>" });
  });
  r.page.on("request", (q) => { if (q.resourceType() === "ping" || q.method() === "POST") calls.beacons++; });
  await r.page.goto(BASE + path, { waitUntil: "networkidle" });
  await r.page.evaluate(() => document.querySelectorAll(".reveal").forEach((e) => e.classList.add("is-visible")));
  return { ...r, calls, setReply: (o) => { reply = { ...reply, ...o }; } };
}

await check("demo CTA in header, home hero and Como funciona point to the app /demo", async () => {
  const { ctx, page } = await demoPage(VIEWPORTS.desktop, "/");
  const hrefs = await page.$$eval("a[data-demo-cta]", (as) => as.map((a) => [a.dataset.demoCta, a.getAttribute("href"), a.dataset.demoStatusUrl]));
  assert.deepEqual(hrefs.map((h) => h[0]).sort(), ["header", "header", "hero", "menu"]);
  for (const [, href, st] of hrefs) {
    assert.equal(href, `${APP}/demo`);
    assert.equal(st, `${APP}/api/public/demo-status`);
  }
  assert.equal(await page.locator('a[data-demo-cta="header"]:visible').count(), 1, "header CTA visible at 1366");
  assert.equal(await page.getAttribute('a[data-demo-cta="header"]:visible', "aria-label"), "Ver demo — abrir a demonstração");
  assert.equal(await page.isVisible('a[data-demo-cta="hero"]'), true);
  await page.goto(BASE + "/como-funciona", { waitUntil: "networkidle" });
  assert.equal(await page.getAttribute('a[data-demo-cta="como-funciona"]', "href"), `${APP}/demo`);
  await ctx.close();
});

await check("demo: header CTA (short label) also shows loading and navigates", async () => {
  const { ctx, page, calls } = await demoPage(VIEWPORTS.desktop, "/planos", { delayMs: 500 });
  const cta = 'a[data-demo-cta="header"]:visible';
  await page.click(cta);
  await page.waitForSelector('a[data-demo-cta="header"][aria-busy="true"]');
  assert.equal((await page.textContent('a[data-demo-cta="header"][aria-busy="true"] [data-demo-label]')).trim(), "Abrindo…");
  await page.waitForURL(`${APP}/demo`);
  assert.equal(calls.status, 1);
  await ctx.close();
});

await check("demo: loading state, then the app /demo without any token in the URL", async () => {
  const { ctx, page, calls, errors } = await demoPage(VIEWPORTS.desktop, "/", { delayMs: 800 });
  const cta = 'a[data-demo-cta="hero"]';
  await page.click(cta);
  await page.waitForSelector(`${cta}[aria-busy="true"]`);
  assert.equal((await page.textContent(`${cta} [data-demo-label]`)).trim(), "Abrindo demonstração…");
  assert.equal(await page.isVisible(`${cta} [data-demo-spinner]`), true);
  await page.screenshot({ path: `${shots}/desktop-demo-carregando.png` });
  await page.waitForURL(`${APP}/demo`);
  assert.equal(page.url(), `${APP}/demo`, "no query/fragment (no token, no session id)");
  assert.equal(calls.status, 1);
  assert.equal(calls.session, 0, "the site never opens the session itself");
  assert.equal(calls.beacons, 0, "no event endpoint configured → nothing sent");
  assert.deepEqual(errors, []);
  await ctx.close();
});

for (const theme of ["dark", "light"]) {
  await check(`demo: unavailable / network error show a message and retry (${theme})`, async () => {
    const { ctx, page, calls, setReply } = await demoPage(VIEWPORTS.desktop, "/", { status: 200, body: { available: false } });
    if (theme === "light") await page.click("[data-theme-toggle]");
    const cta = 'a[data-demo-cta="hero"]';
    await page.click(cta);
    await page.waitForSelector('#demo-status-hero[data-state="error"]');
    assert.match(await page.textContent("#demo-status-hero"), /indisponível no momento/);
    assert.equal((await page.textContent(`${cta} [data-demo-label]`)).trim(), "Tentar de novo");
    assert.equal(await page.getAttribute(cta, "aria-busy"), null);
    assert.equal(page.url(), `${BASE}/`, "stays on the site");
    await page.screenshot({ path: `${shots}/desktop-demo-erro-${theme === "dark" ? "escuro" : "claro"}.png` });
    setReply({ abort: true });
    await page.click(cta);
    await page.waitForFunction(() => /Verifique a conexão/.test(document.querySelector("#demo-status-hero")?.textContent || ""));
    setReply({ abort: false, body: { available: true, plant_running: false } });
    await page.click(cta);                      // planta parada: o app mostra a faixa "temporariamente parada"
    await page.waitForURL(`${APP}/demo`);
    assert.equal(calls.status, 3);
    await ctx.close();
  });
}

await check("demo: mobile menu button opens the demo", async () => {
  const { ctx, page, calls } = await demoPage(VIEWPORTS.mobile, "/");
  assert.equal(await page.locator('a[data-demo-cta="header"]:visible').count(), 0, "header CTA hidden on phones");
  await page.click("[data-menu-toggle]");
  await page.screenshot({ path: `${shots}/mobile-menu-demo.png` });
  await page.click('#menu-mobile a[data-demo-cta="menu"]');
  await page.waitForURL(`${APP}/demo`);
  assert.equal(calls.status, 1);
  await ctx.close();
});

await check("demo: Ctrl/Cmd-click opens the link directly (new tab), no interception", async () => {
  const { ctx, page, calls } = await demoPage(VIEWPORTS.desktop, "/");
  // (a aba nova abre antes de qualquer route do Playwright: aqui só conferimos que o navegador a abriu e que o site
  //  não interceptou — sem consulta de status, a página original fica onde está)
  const popup = ctx.waitForEvent("page");
  await page.click('a[data-demo-cta="hero"]', { modifiers: ["ControlOrMeta"] });
  await popup;
  await page.waitForTimeout(300);
  assert.equal(calls.status, 0);
  assert.equal(page.url(), `${BASE}/`);
  await ctx.close();
});

for (const [w, h, shown] of [[800, 1280, true], [1024, 768, false], [1180, 820, false], [1280, 800, true], [1366, 768, true], [1920, 1080, true]]) {
  await check(`demo: header fits at ${w}×${h}`, async () => {
    const { ctx, page } = await demoPage({ width: w, height: h }, "/como-funciona");
    assert.equal(await page.locator('a[data-demo-cta="header"]:visible').count(), shown ? 1 : 0, "header CTA visibility");
    const tall = await page.$$eval("header .container-x > div a, header .container-x > div button", (els) =>
      els.filter((e) => e.offsetParent && e.getBoundingClientRect().height > 48).map((e) => e.textContent.trim()));
    // 1024–1279 px: o cabeçalho de antes já quebrava "Teste grátis" em 2 linhas (o botão da demo fica oculto aí)
    if (w >= 1280 || w < 1024) assert.deepEqual(tall, [], "header buttons wrapped to two lines");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    assert.ok(overflow <= 0, `overflow ${overflow}`);
    const box = await page.locator("header").boundingBox();
    assert.ok(box.height <= 65, `header wraps (${box.height}px)`);
    await page.screenshot({ path: `${shots}/demo-header-${w}.png` });
    await ctx.close();
  });
}

// ── Arquivos de SEO ─────────────────────────────────────────────────────────
console.log("\n[SEO]");
await check("robots.txt references the sitemap", async () => {
  const txt = await (await fetch(`${BASE}/robots.txt`)).text();
  assert.match(txt, /Sitemap: https:\/\/factoryconnect\.tech\/sitemap-index\.xml/);
});
await check("sitemap lists every page and not the 404", async () => {
  const xml = await (await fetch(`${BASE}/sitemap-0.xml`)).text();
  for (const [p] of PAGES) assert.ok(xml.includes(`<loc>https://factoryconnect.tech${p}</loc>`), `sitemap ${p}`);
  assert.ok(!xml.includes("404"));
});
await check("favicon, icons, manifest and og image are served", async () => {
  for (const f of ["/favicon.svg", "/favicon.ico", "/apple-touch-icon.png", "/icon-512.png", "/og.png", "/site.webmanifest"]) {
    const r = await fetch(BASE + f);
    assert.equal(r.status, 200, f);
  }
  const png = readFileSync(fileURLToPath(new URL("../public/og.png", import.meta.url)));
  assert.equal(png.readUInt32BE(16), 1200); assert.equal(png.readUInt32BE(20), 630);
});

await browser.close();
server?.close();
console.log(`\n${passed} passed, ${failures.length} failed`);
if (failures.length) { console.log("Falhas:\n - " + failures.join("\n - ")); process.exit(1); }
