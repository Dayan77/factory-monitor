/**
 * Tiny, safe Markdown subset for assistant replies (no HTML ever): paragraphs, "- " lists, pipe tables,
 * **bold**, `code`, [label](/internal/route) links and [n] citation markers (IA-2). Pure — unit-tested.
 */

export type Inline =
  | { kind: "text"; text: string }
  | { kind: "bold"; children: Inline[] }
  | { kind: "code"; text: string }
  | { kind: "link"; text: string; href: string; internal: boolean }
  | { kind: "cite"; n: number };

export type Block =
  | { kind: "p"; inlines: Inline[] }
  | { kind: "ul"; items: Inline[][] }
  | { kind: "ol"; items: Inline[][] }
  | { kind: "h"; inlines: Inline[] }
  | { kind: "table"; header: Inline[][]; rows: Inline[][][] };

/** Internal routes only, or http(s). Anything else (javascript:, data:) is rendered as plain text. */
export function safeHref(href: string): { href: string; internal: boolean } | null {
  const h = href.trim();
  if (h.startsWith("/") && !h.startsWith("//")) return { href: h, internal: true };
  if (/^https?:\/\//i.test(h)) return { href: h, internal: false };
  return null;
}

const INLINE = /(\*\*([^*]+)\*\*)|(`([^`]+)`)|(\[([^\]]+)\]\(([^)\s]+)\))|(\[(\d{1,3})\])/g;

export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  let last = 0;
  for (const m of text.matchAll(INLINE)) {
    const i = m.index ?? 0;
    if (i > last) out.push({ kind: "text", text: text.slice(last, i) });
    if (m[1]) out.push({ kind: "bold", children: parseInline(m[2]) });
    else if (m[3]) out.push({ kind: "code", text: m[4] });
    else if (m[5]) {
      const safe = safeHref(m[7]);
      out.push(safe ? { kind: "link", text: m[6], href: safe.href, internal: safe.internal } : { kind: "text", text: m[6] });
    } else if (m[8]) out.push({ kind: "cite", n: Number(m[9]) });
    last = i + m[0].length;
  }
  if (last < text.length) out.push({ kind: "text", text: text.slice(last) });
  return out;
}

function cells(line: string): string[] {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
}

export function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length) blocks.push({ kind: "p", inlines: parseInline(para.join(" ")) });
    para = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const t = line.trim();
    if (!t) { flush(); continue; }
    // table: header row + separator row
    if (t.startsWith("|") && i + 1 < lines.length && /^\|?\s*:?-{2,}/.test(lines[i + 1].trim())) {
      flush();
      const header = cells(t).map(parseInline);
      const rows: Inline[][][] = [];
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith("|")) { rows.push(cells(lines[i]).map(parseInline)); i++; }
      i--;
      blocks.push({ kind: "table", header, rows });
      continue;
    }
    const bullet = /^[-*•]\s+(.*)$/.exec(t);
    const num = /^\d+[.)]\s+(.*)$/.exec(t);
    if (bullet || num) {
      flush();
      const kind = bullet ? "ul" : "ol";
      const prev = blocks[blocks.length - 1];
      const item = parseInline((bullet ?? num)![1]);
      if (prev && prev.kind === kind) prev.items.push(item);
      else blocks.push(kind === "ul" ? { kind: "ul", items: [item] } : { kind: "ol", items: [item] });
      continue;
    }
    const h = /^#{1,4}\s+(.*)$/.exec(t);
    if (h) { flush(); blocks.push({ kind: "h", inlines: parseInline(h[1]) }); continue; }
    para.push(t);
  }
  flush();
  return blocks;
}

/** Plain text for copy/share: markers kept as "[n]" and a sources list appended. */
export function plainAnswer(reply: string, citations: { id: number; label: string; url: string }[], origin: string): string {
  const body = reply.replace(/\*\*([^*]+)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1")
    .replace(/\[([^\]]+)\]\((\/[^)\s]*)\)/g, (_m, l: string, u: string) => `${l} (${origin}${u})`);
  if (!citations.length) return body;
  return `${body}\n\nFontes:\n${citations.map((c) => `[${c.id}] ${c.label} — ${origin}${c.url}`).join("\n")}`;
}

const TOOL_LABEL: Record<string, string> = {
  resolve_asset: "Identificando o ativo",
  get_machine_status: "Consultando o estado das máquinas",
  get_oee: "Consultando o OEE",
  get_losses_pareto: "Consultando o Pareto de perdas",
  get_downtime: "Consultando as paradas",
  get_shift_report: "Consultando o relatório de turno",
  get_component_life: "Consultando a vida dos componentes",
  get_maintenance_due: "Consultando as manutenções a vencer",
  get_work_orders: "Consultando as ordens de serviço",
  get_maintenance_kpis: "Consultando os KPIs de manutenção",
  get_reliability: "Consultando a confiabilidade (MTBF/MCBF/MTTR)",
  search_failures: "Consultando as falhas registradas",
  get_predictive_alerts: "Consultando os alertas preditivos",
  get_condition_trend: "Consultando a tendência de condição",
};

export function toolLabel(name: string): string {
  return TOOL_LABEL[name] ?? `Consultando ${name}`;
}

export const REFUSAL_LABEL: Record<string, string> = {
  no_permission: "Sem permissão",
  insufficient_data: "Dados insuficientes",
  out_of_scope: "Fora do escopo",
  write_not_allowed: "Ação não permitida pelo assistente",
  ambiguous: "Pergunta ambígua",
  budget_exceeded: "Limite do assistente atingido",
};

export interface Suggestion { text: string }

/** Suggested questions by role (ai_assistant.md §5 examples). */
export function suggestionsFor(roles: string[]): string[] {
  const has = (...r: string[]) => r.some((x) => roles.includes(x));
  const out: string[] = [];
  if (has("maintenance_planner")) out.push("Algum componente está degradando? Quando vai falhar?", "O que vence esta semana e o que está atrasado?", "Quais componentes estão perto do fim da vida?");
  if (has("technician")) out.push("Quais são as minhas OS de hoje?", "Quando foi a última falha do cilindro A2?", "Algum componente está degradando? Quando vai falhar?");
  if (has("manager", "admin", "superadmin")) out.push("Como foi o OEE da Linha de Montagem ontem, por turno?", "Estamos mais preventivos ou apagando incêndio? A equipe dá conta?", "Onde perdemos mais tempo na semana passada?");
  if (has("production_supervisor")) out.push("Quais paradas de hoje ainda estão sem motivo?", "O que está parado agora?");
  out.push("O que está parado agora?");
  return [...new Set(out)].slice(0, 6);
}
