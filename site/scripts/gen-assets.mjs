// Gera os ícones PNG/ICO e a imagem Open Graph (1200x630) a partir de SVG/HTML, com Chromium (Playwright).
// Rodar quando a identidade visual mudar:  node scripts/gen-assets.mjs   (arquivos vão para public/, versionados)
import { writeFileSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { existsSync } from "node:fs";
// Playwright: PLAYWRIGHT_MODULE > instalação do container de dev (/opt) > devDependency do projeto (CI).
const OPT_PW = "/opt/node-tools/node_modules/playwright/index.mjs";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || (existsSync(OPT_PW) ? OPT_PW : "playwright"));
const OPT_CHROMIUM = "/opt/pw-browsers/chromium";
const executablePath = process.env.CHROMIUM_PATH || (existsSync(OPT_CHROMIUM) ? OPT_CHROMIUM : undefined);
const pub = (f) => fileURLToPath(new URL(`../public/${f}`, import.meta.url));
const icon = readFileSync(pub("favicon.svg"), "utf8");

const browser = await chromium.launch({ executablePath });
const page = await browser.newPage();

async function shot(html, w, h) {
  await page.setViewportSize({ width: w, height: h });
  await page.setContent(`<!doctype html><html><head><style>html,body{margin:0;width:${w}px;height:${h}px;overflow:hidden}</style></head><body>${html}</body></html>`);
  return page.screenshot({ type: "png", omitBackground: true });
}

const iconHtml = (s, pad = 0) =>
  `<div style="width:${s}px;height:${s}px;display:grid;place-items:center;${pad ? "background:#0a0f0d;" : ""}">${icon.replace("<svg ", `<svg width="${s - pad * 2}" height="${s - pad * 2}" `)}</div>`;

writeFileSync(pub("apple-touch-icon.png"), await shot(iconHtml(180, 18), 180, 180));
writeFileSync(pub("icon-192.png"), await shot(iconHtml(192), 192, 192));
writeFileSync(pub("icon-512.png"), await shot(iconHtml(512), 512, 512));

// favicon.ico com PNGs 16/32/48 embutidos (formato ICO aceita PNG).
const sizes = [16, 32, 48];
const pngs = [];
for (const s of sizes) pngs.push(await shot(iconHtml(s), s, s));
const header = Buffer.alloc(6);
header.writeUInt16LE(0, 0); header.writeUInt16LE(1, 2); header.writeUInt16LE(sizes.length, 4);
let offset = 6 + 16 * sizes.length;
const dir = sizes.map((s, i) => {
  const e = Buffer.alloc(16);
  e.writeUInt8(s, 0); e.writeUInt8(s, 1); e.writeUInt8(0, 2); e.writeUInt8(0, 3);
  e.writeUInt16LE(1, 4); e.writeUInt16LE(32, 6);
  e.writeUInt32LE(pngs[i].length, 8); e.writeUInt32LE(offset, 12);
  offset += pngs[i].length;
  return e;
});
writeFileSync(pub("favicon.ico"), Buffer.concat([header, ...dir, ...pngs]));

const og = `
<div style="width:1200px;height:630px;box-sizing:border-box;padding:80px;background:
  radial-gradient(60% 80% at 85% 10%, rgba(37,163,104,.35), transparent 70%), #0a0f0d;
  font-family: Inter, system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif; color:#ecf3ef; position:relative">
  <div style="position:absolute;inset:0;background-image:linear-gradient(#24302b 1px,transparent 1px),linear-gradient(90deg,#24302b 1px,transparent 1px);background-size:60px 60px;opacity:.35;-webkit-mask-image:radial-gradient(80% 80% at 50% 0%,#000,transparent)"></div>
  <div style="position:relative;display:flex;align-items:center;gap:18px;font-size:38px;font-weight:700">
    ${icon.replace("<svg ", '<svg width="64" height="64" ')}
    <span>Factory<span style="color:#48c085">Connect</span></span>
  </div>
  <div style="position:relative;margin-top:90px;font-size:84px;font-weight:800;letter-spacing:-2px;line-height:1.05">
    O <span style="color:#48c085">raio-x</span> da sua fábrica.
  </div>
  <div style="position:relative;margin-top:28px;font-size:34px;color:#a3b3ab">Eficiência · Manutenção · Uso de cada componente</div>
  <div style="position:relative;margin-top:56px;font-size:24px;color:#a3b3ab;font-family:ui-monospace,monospace">Modbus TCP · OPC UA · MQTT</div>
</div>`;
await page.setViewportSize({ width: 1200, height: 630 });
await page.setContent(`<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0}</style></head><body>${og}</body></html>`);
writeFileSync(pub("og.png"), await page.screenshot({ type: "png" }));

await browser.close();
console.log("ok: favicon.ico, apple-touch-icon.png, icon-192.png, icon-512.png, og.png");
