/**
 * Drill-down context Fábrica → Linha → Máquina → Posição (docs/sprint6.md §2.2): the period travels in the URL
 * (`?period=current_shift|today|yesterday|last_7d|custom&start=&stop=`) through every drill-down link, breadcrumb and
 * back button. "Hoje/ontem" are productive days, resolved by the server (`period=` on /api/oee and /api/losses/pareto).
 */

export type DrillPeriod = "current_shift" | "today" | "yesterday" | "last_7d" | "custom";

export const DRILL_PERIODS: DrillPeriod[] = ["current_shift", "today", "yesterday", "last_7d", "custom"];

export const DRILL_PERIOD_LABELS: Record<DrillPeriod, string> = {
  current_shift: "Turno atual",
  today: "Hoje",
  yesterday: "Ontem",
  last_7d: "7 dias",
  custom: "Personalizado",
};

/** Names used by the older screens' URLs (OEE, timeline) → the drill-down names. */
const ALIASES: Record<string, DrillPeriod> = { shift: "current_shift", "7d": "last_7d" };

export interface DrillContext {
  period: DrillPeriod;
  /** custom only (ISO instants). */
  start?: string;
  stop?: string;
}

const ISO = /^\d{4}-\d{2}-\d{2}(T[\d:.]+(Z|[+-]\d{2}:?\d{2})?)?$/;

export function parseDrill(params: URLSearchParams, fallback: DrillPeriod = "today"): DrillContext {
  const raw = params.get("period") ?? "";
  const period = (DRILL_PERIODS as string[]).includes(raw) ? (raw as DrillPeriod) : ALIASES[raw] ?? fallback;
  if (period !== "custom") return { period };
  const start = params.get("start") ?? params.get("from") ?? "";
  const stop = params.get("stop") ?? params.get("to") ?? "";
  if (ISO.test(start) && ISO.test(stop)) return { period, start, stop };
  return { period: fallback };
}

/** Query string (without "?") of the context — the same on every level. */
export function drillSearch(ctx: DrillContext, extra: Record<string, string | null | undefined> = {}): string {
  const q = new URLSearchParams();
  q.set("period", ctx.period);
  if (ctx.period === "custom" && ctx.start && ctx.stop) {
    q.set("start", ctx.start);
    q.set("stop", ctx.stop);
  }
  for (const [k, v] of Object.entries(extra)) if (v) q.set(k, v);
  return q.toString();
}

/** A drill-down link that keeps the period. */
export function drillLink(path: string, ctx: DrillContext, extra: Record<string, string | null | undefined> = {}): string {
  return `${path}?${drillSearch(ctx, extra)}`;
}

export const drillPaths = {
  plant: () => "/factory",
  plantList: () => "/factory/list",
  line: (lineId: string) => `/factory/lines/${lineId}`,
  machine: (machineId: string) => `/factory/machines/${machineId}`,
};

/** API params of the context (the server resolves productive days: never 00:00–00:00 on the client). */
export function drillApiParams(ctx: DrillContext): { period: DrillPeriod; start?: string; stop?: string } {
  return ctx.period === "custom" ? { period: "custom", start: ctx.start, stop: ctx.stop } : { period: ctx.period };
}

/** Portuguese reason of an undefined KPI (kpis.md §0) and the shortcut that fixes it (§2.2 rule 5). */
export const KPI_REASONS: Record<string, { text: string; fix?: { label: string; to: string } }> = {
  no_planned_time: { text: "sem tempo planejado neste período", fix: { label: "cadastrar turno", to: "/settings/production?tab=shifts" } },
  no_ideal_cycle_time: { text: "produto sem tempo de ciclo ideal", fix: { label: "cadastrar PRI", to: "/settings/production?tab=products" } },
  insufficient_data: { text: "dados insuficientes ou produto sem tempo de ciclo ideal", fix: { label: "cadastrar PRI", to: "/settings/production?tab=products" } },
  no_output_machine: { text: "linha sem máquina de saída", fix: { label: "definir máquina de saída", to: "/lines" } },
  no_shift: { text: "fora do turno: nenhum turno agora", fix: { label: "ver turnos", to: "/settings/production?tab=shifts" } },
  no_production: { text: "nenhuma peça produzida no período" },
};

export function kpiReason(reason: string | null | undefined): { text: string; fix?: { label: string; to: string } } | null {
  if (!reason) return null;
  return KPI_REASONS[reason] ?? { text: reason.replace(/_/g, " ") };
}
