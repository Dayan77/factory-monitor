// QA: every public-site page in BOTH themes (dark default + light via the persisted "fc-theme" choice) on
// desktop and mobile: HTTP 200, theme really applied, no console/page errors, no horizontal overflow, no
// third-party requests. Complements site/tests/e2e.mjs (which sweeps pages in the default dark theme only).
//   cd site && npx astro build && node ../e2e/site_themes.verify.mjs     (BASE_URL=... to test another server)
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
const here = path.dirname(fileURLToPath(import.meta.url));
const PW = existsSync("/opt/node-tools/node_modules/playwright/index.mjs") ? "/opt/node-tools/node_modules/playwright/index.mjs" : "playwright";
const { chromium } = await import(PW);
const { serve } = await import(path.join(here, "../site/tests/serve.mjs"));
const PORT = Number(process.env.E2E_PORT || 5263);
let server, BASE = process.env.BASE_URL;
if (!BASE) { server = await serve(path.join(here, "../site/dist"), PORT); BASE = `http://127.0.0.1:${PORT}`; }
const host = new URL(BASE).host;
const PAGES = ["/", "/solucao", "/para-manutencao", "/para-producao", "/como-funciona", "/seguranca", "/planos", "/sobre", "/contato", "/privacidade"];
const VPS = { desktop: { width: 1366, height: 900 }, mobile: { width: 390, height: 844, isMobile: true, hasTouch: true } };
const browser = await chromium.launch({ executablePath: existsSync("/opt/pw-browsers/chromium") ? "/opt/pw-browsers/chromium" : undefined });
let passed = 0; const failed = [];
for (const theme of ["dark", "light"]) for (const [vp, opts] of Object.entries(VPS)) {
  const ctx = await browser.newContext({ viewport: { width: opts.width, height: opts.height }, isMobile: opts.isMobile, hasTouch: opts.hasTouch });
  await ctx.addInitScript((t) => { try { localStorage.setItem("fc-theme", t); } catch {} }, theme);
  const page = await ctx.newPage();
  const errors = [], external = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("request", (r) => { const u = new URL(r.url()); if (/^https?:$/.test(u.protocol) && u.host !== host) external.push(r.url()); });
  for (const p of PAGES) {
    errors.length = 0;
    const res = await page.goto(BASE + p, { waitUntil: "networkidle" });
    const st = await page.evaluate(() => ({ dark: document.documentElement.classList.contains("dark"), over: document.documentElement.scrollWidth - window.innerWidth, bg: getComputedStyle(document.body).backgroundColor }));
    const probs = [];
    if (res.status() !== 200) probs.push(`status ${res.status()}`);
    if (st.dark !== (theme === "dark")) probs.push(`theme not applied (dark=${st.dark})`);
    if (st.over > 0) probs.push(`horizontal overflow ${st.over}px`);
    if (errors.length) probs.push(`console: ${errors.join(" | ")}`);
    const name = `${theme} ${vp} ${p}`;
    if (probs.length) { failed.push(name); console.log(`  ✗ ${name}: ${probs.join("; ")}`); } else { passed++; console.log(`  ✓ ${name} (bg ${st.bg})`); }
  }
  if (external.length) { failed.push(`${theme} ${vp} third-party`); console.log(`  ✗ third-party requests ${external}`); } else passed++;
  await ctx.close();
}
await browser.close(); server?.close?.();
console.log(`\n${passed} passed, ${failed.length} failed`);
process.exit(failed.length ? 1 : 0);
