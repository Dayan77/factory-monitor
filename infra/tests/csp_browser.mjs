#!/usr/bin/env node
// Abre páginas num Chromium de verdade e falha se o navegador registrar QUALQUER violação de CSP
// (evento securitypolicyviolation + mensagens "Content Security Policy" do console) ou se a página não renderizar.
// Usado por infra/tests/test_security_headers.py contra o nginx real (web/site) com os cabeçalhos de produção.
//
//   node infra/tests/csp_browser.mjs <url> [<url> ...]     → imprime JSON {url: {violations, console, ok}}
// Playwright: PLAYWRIGHT_MODULE > /opt/node-tools (container de dev) > "playwright" resolvido do cwd (CI: site/).
import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const OPT_PW = "/opt/node-tools/node_modules/playwright/index.mjs";
let mod = process.env.PLAYWRIGHT_MODULE || (existsSync(OPT_PW) ? OPT_PW : null);
if (!mod) mod = pathToFileURL(createRequire(`${process.cwd()}/`).resolve("playwright")).href;
// O "playwright" resolvido via require é CommonJS: no import() dinâmico os exports podem vir em `default`.
const pw = await import(mod);
const chromium = pw.chromium ?? pw.default?.chromium;
const OPT_CHROMIUM = "/opt/pw-browsers/chromium";
const executablePath = process.env.CHROMIUM_PATH || (existsSync(OPT_CHROMIUM) ? OPT_CHROMIUM : undefined);

const urls = process.argv.slice(2);
if (!urls.length) {
  console.error("uso: csp_browser.mjs <url> [...]");
  process.exit(2);
}
const browser = await chromium.launch({ executablePath });
const out = {};
try {
  for (const url of urls) {
    const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
    await ctx.addInitScript(() => {
      window.__cspViolations = [];
      document.addEventListener("securitypolicyviolation", (e) => {
        window.__cspViolations.push(`${e.violatedDirective} blocked=${e.blockedURI} sample=${e.sample}`);
      });
    });
    const page = await ctx.newPage();
    const consoleCsp = [];
    page.on("console", (m) => { if (/Content Security Policy/i.test(m.text())) consoleCsp.push(m.text()); });
    let ok = true, error = "";
    try {
      const resp = await page.goto(url, { waitUntil: "load", timeout: 30000 });
      ok = !!resp && resp.status() < 400;
      await page.waitForTimeout(1500);           // scripts de módulo, service worker, fetch inicial
      const bodyText = (await page.evaluate(() => document.body?.innerText || "")).trim();
      if (!bodyText) { ok = false; error = "página em branco (script bloqueado?)"; }
    } catch (e) {
      ok = false; error = String(e);
    }
    const violations = await page.evaluate(() => window.__cspViolations || []).catch(() => []);
    out[url] = { ok, error, violations, console: consoleCsp };
    await ctx.close();
  }
} finally {
  await browser.close();
}
console.log(JSON.stringify(out));
