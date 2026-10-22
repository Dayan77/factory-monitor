/**
 * Texto do manual para a busca (sem dependência do build: testado em tests/docs.test.ts).
 */
import type { MarkdownHeading } from "astro";

export interface IndexableDoc { short: string; url: string; raw: string; headings: MarkdownHeading[] }

/** "Operador (tablet da linha)" → "Operador"; "Planejador de manutenção (PCM)" → "Planejador de manutenção". */
export function shortTitle(title: string): string {
  return title.replace(/\s*\(.*\)\s*$/, "").replace(/^Manual do usuário FactoryConnect$/, "Início").trim();
}

export interface SearchEntry {
  /** Página */
  p: string;
  /** Título da seção */
  h: string;
  /** URL com âncora */
  u: string;
  /** Texto da seção (sem markdown), para a busca */
  t: string;
}

/** Markdown → texto simples (para o índice de busca). */
export function plainText(md: string): string {
  return md
    .replace(/<a id="[^"]*"><\/a>/g, " ")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/<[^>]+>/g, " ")
    .replace(/^\s*[|>#*-]+|\|/gm, " ")
    .replace(/[*_~]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Índice de busca por seção (h2/h3): cada entrada aponta para a âncora da seção. A âncora é a explícita do manual
 * (`<a id="oee"></a>` logo antes do título, estável) ou, sem ela, o id gerado pelo Astro para o título.
 */
export function buildSearchIndex(docs: IndexableDoc[], maxText = 600): SearchEntry[] {
  const out: SearchEntry[] = [];
  for (const d of docs) {
    const body = d.raw.replace(/^---[\s\S]*?---\s*/, "");
    const lines = body.split("\n");
    let hi = 0;
    let inCode = false;
    let cur: { h: string; anchor: string; text: string[] } = { h: d.short, anchor: "", text: [] };
    let pendingAnchor = "";
    const flush = () => {
      const t = plainText(cur.text.join("\n"));
      if (t || cur.anchor) out.push({ p: d.short, h: cur.h, u: `${d.url}${cur.anchor ? `#${cur.anchor}` : ""}`, t: t.slice(0, maxText) });
    };
    for (const line of lines) {
      if (/^```/.test(line)) inCode = !inCode;
      const a = /^<a id="([^"]+)"><\/a>\s*$/.exec(line.trim());
      if (!inCode && a) { pendingAnchor = a[1] ?? ""; continue; }
      const m = !inCode && /^(#{1,6})\s+(.*)$/.exec(line);
      if (m) {
        const depth = (m[1] ?? "").length;
        const gen = d.headings.find((h, i) => i >= hi && h.depth === depth);
        if (gen) hi = d.headings.indexOf(gen) + 1;
        if (depth >= 2 && depth <= 3) {
          flush();
          cur = { h: plainText(m[2] ?? ""), anchor: pendingAnchor || gen?.slug || "", text: [] };
        }
        pendingAnchor = "";
        continue;
      }
      if (line.trim()) pendingAnchor = "";
      cur.text.push(line);
    }
    flush();
  }
  return out;
}

