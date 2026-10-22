// QA: every page in LIGHT theme (desktop + mobile): 200, no console errors, no horizontal overflow.
// Complements tests/e2e.mjs (which checks pages in the default dark theme). Usage: npm run build && node tests/qa_light.mjs
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { serve } from "./serve.mjs";
const OPT_PW = "/opt/node-tools/node_modules/playwright/index.mjs";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || (existsSync(OPT_PW) ? OPT_PW : "playwright"));
const exe = existsSync("/opt/pw-browsers/chromium") ? "/opt/pw-browsers/chromium" : undefined;
const PORT = Number(process.env.E2E_PORT || 5251);
const server = await serve(fileURLToPath(new URL("../dist", import.meta.url)), PORT);
const BASE = `http://127.0.0.1:${PORT}`;
const PAGES = ["/", "/solucao", "/para-manutencao", "/para-producao", "/como-funciona", "/seguranca", "/planos", "/sobre", "/contato", "/privacidade", "/nao-existe"];
const browser = await chromium.launch({ executablePath: exe });
let fail = 0;
for (const [name, vp] of Object.entries({ desktop: { width: 1366, height: 900 }, mobile: { width: 390, height: 844 } })) {
  const ctx = await browser.newContext({ viewport: vp, isMobile: name === "mobile", colorScheme: "light" });
  await ctx.addInitScript(() => { try { localStorage.setItem("fc-theme", "light"); } catch {} });
  const page = await ctx.newPage();
  for (const p of PAGES) {
    const errors = [];
    const onC = (m) => { if (m.type() === "error" && !/404/.test(m.text())) errors.push(m.text()); };
    page.on("console", onC); page.on("pageerror", (e) => errors.push(String(e)));
    const r = await page.goto(BASE + p, { waitUntil: "networkidle" });
    // force light via the toggle if the stored key differs
    let theme = await page.evaluate(() => document.documentElement.dataset.theme || (document.documentElement.classList.contains("dark") ? "dark" : "light"));
    if (theme !== "light") {
      const t = page.getByRole("button", { name: /tema|claro|escuro|theme/i }).first();
      if (await t.count()) { await t.click(); await page.waitForTimeout(200); }
      theme = await page.evaluate(() => document.documentElement.dataset.theme || (document.documentElement.classList.contains("dark") ? "dark" : "light"));
    }
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    const ok = (p === "/nao-existe" ? r.status() === 404 : r.status() === 200) && errors.length === 0 && !overflow && theme === "light";
    if (!ok) fail++;
    console.log(`  ${ok ? "✓" : "✗"} ${name} ${p} status=${r.status()} theme=${theme} overflow=${overflow} errors=${errors.length}${errors.length ? " " + errors[0] : ""}`);
    page.off("console", onC);
  }
  await ctx.close();
}
await browser.close(); server.close?.();
console.log(fail ? `${fail} failed` : "all light-theme checks passed");
process.exit(fail ? 1 : 0);
