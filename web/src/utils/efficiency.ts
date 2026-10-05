/**
 * Pure helpers of the efficiency screens (OEE, losses, stops). No React here — unit-tested.
 */

import type { DowntimeEvent, OeeResult, ParetoRow, ReasonCode } from "@/services/types";
import { formatPct } from "@/utils/format";

// ── formatting ────────────────────────────────────────────────────

const dec1 = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

/** Seconds → "45 s", "12 min", "1 h 05 min", "2 d 3 h". Null → "—". */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const min = Math.floor(s / 60);
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h} h ${String(min % 60).padStart(2, "0")} min`;
  return `${Math.floor(h / 24)} d ${h % 24} h`;
}

/** Compact duration for chart labels: "53 min", "5,9 h". */
export function formatShortDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const s = Math.max(0, seconds);
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${dec1.format(s / 3600)} h`;
}

/** Seconds → stopwatch "04:07" / "1:04:07" (TV mode, open stop timer). */
export function formatTimer(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** Hours → "12,3 h". */
export function formatHours(h: number | null | undefined): string {
  return h == null || !Number.isFinite(h) ? "—" : `${dec1.format(h)} h`;
}

/** Ratio (0..1, may exceed 1) → "72,4 %" with one decimal (kpis.md S3-5; same as formatPct). */
export function formatPct1(r: number | null | undefined): string {
  return formatPct(r);
}

export type KpiTone = "ok" | "warn" | "danger" | "neutral";

/**
 * OEE color band: ≥ 85% "classe mundial" (ok), 60–85% típico (warn), < 60% (danger), null neutral.
 * The same bands are used for A, P and Q so the card reads at a glance.
 */
export function oeeTone(r: number | null | undefined): KpiTone {
  if (r == null || !Number.isFinite(r)) return "neutral";
  if (r >= 0.85) return "ok";
  if (r >= 0.6) return "warn";
  return "danger";
}

export const TONE_TEXT: Record<KpiTone, string> = {
  ok: "text-ok", warn: "text-warn", danger: "text-danger", neutral: "text-fg-muted",
};
export const TONE_FILL: Record<KpiTone, string> = {
  ok: "rgb(var(--c-ok-solid))", warn: "rgb(var(--c-warn-solid))", danger: "rgb(var(--c-danger-solid))",
  neutral: "rgb(var(--c-neutral-solid))",
};

// ── reason codes ──────────────────────────────────────────────────

export const CATEGORY_LABELS: Record<string, string> = {
  EQP: "Equipamento",
  SET: "Setup e ajuste",
  MAT: "Material e fluxo",
  OPR: "Operador",
  QLT: "Qualidade",
  UTL: "Utilidades",
  MNT: "Manutenção",
  PLN: "Paradas planejadas",
  UNK: "Não justificada",
  micro_stops: "Micro-paradas",
  speed_loss: "Perda de velocidade",
  quality_loss: "Qualidade (refugo)",
};

/** Picker order: the most frequent unplanned causes first, planned last. */
const CATEGORY_ORDER = ["EQP", "SET", "MAT", "OPR", "QLT", "UTL", "MNT", "PLN"];

export function categoryLabel(cat: string): string {
  return CATEGORY_LABELS[cat] ?? cat;
}

export interface ReasonGroup {
  category: string;
  label: string;
  codes: ReasonCode[];
}

/** Active reason codes grouped by category for the operator picker ("Não justificada" excluded). */
export function groupReasonCodes(codes: ReasonCode[]): ReasonGroup[] {
  const by = new Map<string, ReasonCode[]>();
  for (const c of codes) {
    if (!c.is_active || c.category === "UNK" || c.code === "UNK-01") continue;
    const list = by.get(c.category) ?? [];
    list.push(c);
    by.set(c.category, list);
  }
  const rank = (cat: string) => {
    const i = CATEGORY_ORDER.indexOf(cat);
    return i < 0 ? CATEGORY_ORDER.length - 1 : i; // tenant categories before "planned"
  };
  return [...by.entries()]
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
    .map(([category, list]) => ({
      category,
      label: categoryLabel(category),
      codes: list.slice().sort((a, b) => a.code.localeCompare(b.code)),
    }));
}

export function reasonLabel(code: string | null | undefined, codes: ReasonCode[] | undefined): string {
  if (!code || code === "UNK-01") return "Não justificada";
  if (code in CATEGORY_LABELS) return CATEGORY_LABELS[code];
  return codes?.find((c) => c.code === code)?.label ?? code;
}

// ── Pareto ────────────────────────────────────────────────────────

export interface ParetoBar {
  key: string;
  label: string;
  category: string;
  /** Loss in minutes (chart unit). */
  minutes: number;
  duration_s: number;
  events: number;
  /** Share of the total loss, 0..100. */
  pct: number;
  /** Cumulative share, 0..100. */
  cumulative: number;
  pseudo: boolean;
}

/**
 * Shapes Pareto rows for the chart: drops planned and zero rows, sorts descending, keeps the top
 * `maxBars - 1` and folds the tail into "Outros" so the cumulative line still ends at 100%.
 * Shares are recomputed from durations (the server's pct is used only for ordering ties).
 */
export function shapePareto(rows: ParetoRow[], maxBars = 8): ParetoBar[] {
  const items = rows
    .filter((r) => !r.planned && r.duration_s > 0)
    .slice()
    .sort((a, b) => b.duration_s - a.duration_s);
  const total = items.reduce((s, r) => s + r.duration_s, 0);
  if (total <= 0) return [];
  const head = items.length > maxBars ? items.slice(0, maxBars - 1) : items;
  const tail = items.length > maxBars ? items.slice(maxBars - 1) : [];
  const bars: Omit<ParetoBar, "pct" | "cumulative">[] = head.map((r) => ({
    key: r.reason_code ?? r.category,
    label: r.label,
    category: r.category,
    minutes: r.duration_s / 60,
    duration_s: r.duration_s,
    events: r.events,
    pseudo: !!r.reason_code && ["micro_stops", "speed_loss", "quality_loss"].includes(r.reason_code),
  }));
  if (tail.length) {
    const d = tail.reduce((s, r) => s + r.duration_s, 0);
    bars.push({
      key: "__others", label: `Outros (${tail.length})`, category: "", minutes: d / 60, duration_s: d,
      events: tail.reduce((s, r) => s + r.events, 0), pseudo: false,
    });
  }
  let acc = 0;
  return bars.map((b, i) => {
    acc += b.duration_s;
    // computed from the raw durations; the last row is exactly 100 % (S3-5)
    const cumulative = i === bars.length - 1 ? 100 : (acc / total) * 100;
    return { ...b, pct: (b.duration_s / total) * 100, cumulative };
  });
}

// ── time waterfall (ISO 22400) ────────────────────────────────────

export interface WaterfallStep {
  key: string;
  label: string;
  /** "total" bars start at 0; "loss" bars float from `base` to `base + value`. */
  kind: "total" | "loss";
  value_s: number;
  base_s: number;
  hint: string;
}

/**
 * POT → PDOT → (sem dados) → PBT → ADOT → APT → micro-paradas → velocidade → qualidade → tempo
 * produtivo líquido. Losses are clamped so the staircase never goes below zero even if the server
 * components don't add up exactly (P > 1 can make speed loss 0).
 */
export function buildWaterfall(o: OeeResult): WaterfallStep[] {
  const nodata = o.nodata_s ?? Math.max(o.pot_s - o.pdot_s - o.pbt_s, 0);
  const steps: WaterfallStep[] = [];
  const total = (key: string, label: string, v: number, hint: string) =>
    steps.push({ key, label, kind: "total", value_s: Math.max(v, 0), base_s: 0, hint });
  let level = o.pot_s;
  const loss = (key: string, label: string, v: number, hint: string) => {
    const val = Math.max(0, Math.min(v, level));
    level -= val;
    steps.push({ key, label, kind: "loss", value_s: val, base_s: level, hint });
  };
  total("pot", "POT", o.pot_s, "Tempo programado (turnos do calendário)");
  loss("pdot", "Planejadas", o.pdot_s, "PDOT — pausas e paradas planejadas");
  if (nodata > 0) loss("nodata", "Sem dados", nodata, "Tempo sem leitura válida — sai do PBT");
  level = o.pbt_s;
  total("pbt", "PBT", o.pbt_s, "Tempo de produção planejado");
  loss("adot", "Paradas", o.adot_s, "ADOT — paradas não planejadas (perda de disponibilidade)");
  level = o.apt_s;
  total("apt", "APT", o.apt_s, "Tempo rodando");
  loss("micro", "Micro-paradas", o.micro_stop_loss_s, "Paradas curtas dentro do APT (perda de desempenho)");
  loss("speed", "Velocidade", o.speed_loss_s, "Ritmo abaixo do ciclo ideal (perda de desempenho)");
  if (o.quality_loss_s != null) loss("quality", "Refugo", o.quality_loss_s, "Tempo ideal das peças refugadas (perda de qualidade)");
  const net = o.net_productive_s ?? level;
  total("net", "Produtivo", net, "Tempo produtivo líquido (Σ PRI × boas)");
  return steps;
}

// ── machine state timeline ────────────────────────────────────────

export type TimelineState = "running" | "stopped" | "error" | "planned" | "nodata";

export interface TimelineSegment {
  state: TimelineState;
  start: number;
  end: number;
  event?: DowntimeEvent;
}

export const TIMELINE_LABELS: Record<TimelineState, string> = {
  running: "Sem parada registrada",
  stopped: "Parada",
  error: "Falha",
  planned: "Parada planejada",
  nodata: "Sem dados / futuro",
};

export function eventState(ev: Pick<DowntimeEvent, "planned" | "fault_code" | "source" | "reason_code">): TimelineState {
  if (ev.planned) return "planned";
  if (ev.fault_code != null || ev.source === "auto_fault" || (ev.reason_code ?? "").startsWith("EQP-")) return "error";
  return "stopped";
}

/**
 * Builds the state strip of one machine over [start, stop): downtime events become stopped / error /
 * planned segments; time between them is "running" (no stop recorded) up to `now`, and the part of
 * the window after `now` is "nodata". Overlapping events are clipped, open events run until `now`.
 */
export function buildTimeline(events: DowntimeEvent[], start: number, stop: number, now: number): TimelineSegment[] {
  const horizon = Math.min(stop, now);
  const segs: TimelineSegment[] = [];
  let cursor = start;
  const push = (state: TimelineState, a: number, b: number, event?: DowntimeEvent) => {
    if (b <= a) return;
    const last = segs[segs.length - 1];
    if (last && !event && !last.event && last.state === state && last.end === a) last.end = b;
    else segs.push({ state, start: a, end: b, event });
  };
  const sorted = events
    .map((e) => ({ e, a: Date.parse(e.start), b: e.end ? Date.parse(e.end) : now }))
    .filter((x) => !Number.isNaN(x.a) && !Number.isNaN(x.b))
    .sort((x, y) => x.a - y.a);
  for (const { e, a, b } of sorted) {
    const s = Math.max(a, cursor, start);
    const t = Math.min(b, horizon);
    if (t <= s) continue;
    push("running", cursor, s);
    push(eventState(e), s, t, e);
    cursor = t;
  }
  push("running", cursor, horizon);
  push("nodata", Math.max(cursor, horizon), stop);
  return segs;
}

// ── trend labels ──────────────────────────────────────────────────

/**
 * Short x-axis label for an OEE trend point, in the plant timezone: "14h" (hour), "05/10" (day);
 * shift buckets keep the server label (it carries the shift name).
 */
export function trendLabel(p: { t: string; label: string }, groupBy: string, tz: string): string {
  const t = Date.parse(p.t);
  if (Number.isNaN(t) || groupBy === "shift") return p.label;
  if (groupBy === "day") {
    return new Intl.DateTimeFormat("pt-BR", { timeZone: tz, day: "2-digit", month: "2-digit" }).format(t);
  }
  const hh = new Intl.DateTimeFormat("pt-BR", { timeZone: tz, hour: "2-digit", hourCycle: "h23" }).format(t);
  return `${hh}h`;
}
