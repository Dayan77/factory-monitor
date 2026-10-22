#!/usr/bin/env node
/**
 * Copia (com os links ajustados para o site) o manual do usuário (docs/manual/*.md, fonte única) para site/src/manual/, de onde a seção
 * "Documentação" do site é gerada no build. Roda antes de `astro dev` / `astro build` (predev / prebuild).
 *
 * Por que copiar: a imagem Docker do site é construída com contexto `site/` (sem acesso a ../docs). As cópias ficam
 * versionadas e o teste tests/docs.test.ts falha se divergirem da fonte — quem edita o manual roda `npm run sync-docs`.
 *
 *   node scripts/sync-docs.mjs          copia (sem ../docs/manual: mantém as cópias versionadas)
 *   node scripts/sync-docs.mjs --check  só confere; sai com 1 se alguma cópia estiver desatualizada
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { rewriteDocLinks } from "../src/lib/doc-links.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.resolve(here, "../../docs/manual");
const DST = path.resolve(here, "../src/manual");
const check = process.argv.includes("--check");

if (!fs.existsSync(SRC)) {
  console.log(`sync-docs: ${SRC} não existe (build isolado) — usando as cópias de ${path.relative(process.cwd(), DST)}`);
  process.exit(0);
}
fs.mkdirSync(DST, { recursive: true });
const src = fs.readdirSync(SRC).filter((f) => f.endsWith(".md")).sort();
const dst = fs.readdirSync(DST).filter((f) => f.endsWith(".md")).sort();
let stale = 0;
for (const f of src) {
  // links entre páginas (escritos para o GitHub) → URLs do site; links para docs técnicos → texto
  const a = rewriteDocLinks(fs.readFileSync(path.join(SRC, f), "utf8"));
  const target = path.join(DST, f);
  const b = fs.existsSync(target) ? fs.readFileSync(target, "utf8") : null;
  if (a === b) continue;
  stale += 1;
  if (check) console.error(`sync-docs: ${f} desatualizado`);
  else fs.writeFileSync(target, a);
}
for (const f of dst.filter((f) => !src.includes(f))) {
  stale += 1;
  if (check) console.error(`sync-docs: ${f} não existe mais em docs/manual`);
  else fs.unlinkSync(path.join(DST, f));
}
if (check && stale) process.exit(1);
console.log(`sync-docs: ${src.length} arquivo(s)${check ? " conferidos" : `, ${stale} atualizado(s)`}`);
