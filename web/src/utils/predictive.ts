/**
 * Pure helpers of the predictive screens (docs/predictive.md). No React here — unit-tested.
 * Principle 4 of the spec: never show fake data — no baseline → "aprendendo", no trend → RUL null with its reason.
 */
import type {
  AlertState, AlertTrigger, CloseResolution, ConditionRul, Feature, FeatureSeries, Monitor, PredictiveAlert,
  PredSeverity, Resolution, RuleType,
} from "@/services/predictiveTypes";
import type { Tone } from "@/components/ui/Badge";
import { formatDate, formatDec1, formatInt } from "@/utils/format";

export const FEATURES: Feature[] = ["stroke_time", "current", "vibration", "temperature"];

export const FEATURE_LABEL: Record<Feature, string> = {
  stroke_time: "Tempo de curso",
  current: "Corrente",
  vibration: "Vibração",
  temperature: "Temperatura",
};

export const FEATURE_UNIT: Record<Feature, string> = { stroke_time: "ms", current: "A", vibration: "mm/s", temperature: "°C" };

export const SEVERITY_LABEL: Record<PredSeverity, string> = { watch: "Atenção", warning: "Alerta", critical: "Crítico" };
export const SEVERITY_TONE: Record<PredSeverity, Tone> = { watch: "info", warning: "warn", critical: "danger" };
export const SEVERITY_RANK: Record<PredSeverity, number> = { watch: 0, warning: 1, critical: 2 };
export const SEVERITY_HINT: Record<PredSeverity, string> = {
  watch: "Detector estatístico — resumo diário, não sugere OS",
  warning: "Regra de engenharia ou RUL ≤ 30 dias — sugere OS preditiva",
  critical: "Regra crítica ou RUL pessimista ≤ 7 dias — avisa o técnico de plantão",
};

export const ALERT_STATE_LABEL: Record<AlertState, string> = {
  open: "Aberto",
  acknowledged: "Reconhecido",
  converted: "Com OS",
  closed: "Fechado",
};
export const ALERT_STATE_TONE: Record<AlertState, Tone> = { open: "danger", acknowledged: "warn", converted: "info", closed: "neutral" };

export const RESOLUTION_LABEL: Record<Resolution, string> = {
  confirmed: "Defeito confirmado",
  false_alarm: "Falso alarme",
  failed_before_action: "Falhou antes da ação",
  rebaseline: "Novo normal (reiniciar linha de base)",
  duplicate: "Duplicado",
  known_cause: "Causa conhecida",
  auto_cleared: "Normalizou sozinho",
};

export const CLOSE_RESOLUTIONS: { value: CloseResolution; label: string; hint: string }[] = [
  { value: "false_alarm", label: "Falso alarme", hint: "Silencia esta posição/sinal por 7 dias para a mesma severidade." },
  { value: "rebaseline", label: "Novo normal", hint: "Ex.: mudança de produto ou carga. Reinicia a linha de base (sem aquecimento); motivo obrigatório." },
  { value: "known_cause", label: "Causa conhecida", hint: "Ex.: teste de engenharia." },
  { value: "duplicate", label: "Duplicado", hint: "Já tratado em outro alerta." },
];

export const RULE_TYPE_LABEL: Record<RuleType, string> = {
  threshold: "Limite absoluto",
  pct_of_baseline: "% da referência",
  rate_of_change: "Taxa de subida",
};

const REASON_PT: Record<string, string> = {
  no_significant_trend: "sem tendência de degradação significativa",
  insufficient_data: "histórico insuficiente (menos de 10 janelas desde o reinício)",
  no_failure_limit: "sem limite de falha configurado",
  no_usage_rate: "sem uso nos últimos 14 dias — não dá para projetar",
  baseline_learning: "linha de base em aprendizado",
  no_recent_usage: "sem uso nos últimos 14 dias — não dá para projetar",
};

export function rulReasonText(reason: string | null | undefined): string {
  if (!reason) return "sem projeção";
  return REASON_PT[reason] ?? reason;
}

const DETECTOR_LABEL: Record<string, string> = {
  EWMA: "EWMA",
  CUSUM: "CUSUM",
  ROLLING_Z: "z robusto móvel (anomalias)",
  RUL_WARNING: "RUL ≤ 30 dias",
  RUL_CRITICAL: "RUL pessimista ≤ 7 dias",
};

const ACTION_LABEL: Record<string, string> = {
  open: "Aberto",
  escalate: "Severidade subiu",
  escalation: "Severidade subiu",
  acknowledge: "Reconhecido",
  convert: "OS preditiva gerada",
  unconvert: "OS cancelada — alerta volta a reconhecido",
  close: "Fechado",
  auto_cleared: "Fechado automaticamente (normalizou)",
};

/** History action → label. */
export function actionLabel(action: string): string {
  return ACTION_LABEL[action] ?? action;
}

/** Detector code or rule code → readable label ("EWMA", "Regra PCT-110"). */
export function triggerLabel(source: string): string {
  return DETECTOR_LABEL[source] ?? `Regra ${source}`;
}

export function isStatisticalTrigger(source: string): boolean {
  return source === "EWMA" || source === "CUSUM" || source === "ROLLING_Z";
}

/** Signal value with unit, pt-BR: 206.5 ms → "206,5 ms". Two decimals for small analog values. */
export function formatSignal(v: number | null | undefined, unit: string | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  const n = abs !== 0 && abs < 1
    ? new Intl.NumberFormat("pt-BR", { maximumSignificantDigits: 2 }).format(v)
    : abs !== 0 && abs < 10
      ? new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v)
      : formatDec1(v);
  return unit ? `${n} ${unit}` : n;
}

/** z / EWMA / CUSUM values (dimensionless, σ units): "+3,25" / "1,34". */
export function formatStat(v: number | null | undefined, signed = false): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const s = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v);
  return signed && v > 0 ? `+${s}` : s;
}

/** "> 1 ano" above 365 days (P-6). */
export function daysText(d: number | null | undefined): string {
  if (d == null) return "—";
  if (d > 365) return "> 1 ano";
  return `${formatInt(d)} ${d === 1 ? "dia" : "dias"}`;
}

export interface RulView {
  /** Headline: "em 11 dias", "limite de falha atingido", or null when not projectable. */
  headline: string | null;
  /** "09/02/2027 (faixa 06/02/2027 – 14/02/2027)". */
  detail: string | null;
  /** "11 dias (8–17)" — compact. */
  compact: string;
  reason: string | null;
  tone: Tone;
}

export function rulView(r: ConditionRul | null | undefined): RulView {
  if (!r) return { headline: null, detail: null, compact: "—", reason: "sem projeção", tone: "neutral" };
  if (r.limit_reached || r.rul_days === 0) {
    return { headline: "limite de falha atingido", detail: r.projected_date ? `em ${formatDate(r.projected_date)}` : null, compact: "limite atingido", reason: null, tone: "danger" };
  }
  if (r.rul_days == null) return { headline: null, detail: null, compact: "—", reason: rulReasonText(r.reason), tone: "neutral" };
  const lo = r.rul_days_low, hi = r.rul_days_high;
  const range = lo != null || hi != null ? ` (${lo != null ? formatInt(lo) : "?"}–${hi == null ? "?" : hi > 365 ? "> 1 ano" : formatInt(hi)})` : "";
  const dates = r.projected_date
    ? `${formatDate(r.projected_date)}${r.projected_date_low || r.projected_date_high ? ` (faixa ${formatDate(r.projected_date_low)} – ${r.projected_date_high ? formatDate(r.projected_date_high) : "?"})` : ""}`
    : null;
  const tone: Tone = (lo != null && lo <= 7) ? "danger" : r.rul_days <= 30 ? "warn" : "ok";
  return { headline: `em ${daysText(r.rul_days)}`, detail: dates, compact: `${daysText(r.rul_days)}${range}`, reason: null, tone };
}

/** Trend in human units (P-5): stroke_time per 10 000 cycles; analog per operating hour. */
export function slopeText(slope: number | null | undefined, feature: Feature, unit: string | null | undefined): string | null {
  if (slope == null || !Number.isFinite(slope)) return null;
  const u = unit ?? FEATURE_UNIT[feature];
  if (feature === "stroke_time") return `${slope >= 0 ? "+" : ""}${formatSignal(slope * 10_000, u)} a cada 10.000 ciclos`;
  return `${slope >= 0 ? "+" : ""}${formatSignal(slope, u)} por hora de operação`;
}

export function windowText(w: { cycles?: number | null; hours?: number | null } | null | undefined): string {
  if (!w) return "—";
  if (w.cycles) return `${formatInt(w.cycles)} ciclos`;
  if (w.hours) return `${formatDec1(w.hours)} h`.replace(",0 h", " h");
  return "—";
}

export function ruleText(r: { type: RuleType; value: number; sustained_for: number }, unit: string, m0?: number | null): string {
  const sus = r.sustained_for > 1 ? ` por ${r.sustained_for} janelas seguidas` : "";
  if (r.type === "threshold") return `> ${formatSignal(r.value, unit)}${sus}`;
  if (r.type === "pct_of_baseline") {
    const abs = m0 != null ? ` (${formatSignal(r.value * m0, unit)})` : "";
    return `> ${formatDec1(r.value * 100).replace(",0", "")} % da referência${abs}${sus}`;
  }
  return `subindo mais de ${formatSignal(r.value, unit)} por unidade de uso${sus}`;
}

/** Open first, by severity (critical first), newest first. Closed at the end. */
export function sortAlerts(list: PredictiveAlert[]): PredictiveAlert[] {
  return list.slice().sort((a, b) =>
    Number(a.state === "closed") - Number(b.state === "closed")
    || SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity]
    || Date.parse(b.opened_at) - Date.parse(a.opened_at));
}

export function countBySeverity(list: Pick<PredictiveAlert, "severity" | "state">[]): Record<PredSeverity, number> {
  const out: Record<PredSeverity, number> = { watch: 0, warning: 0, critical: 0 };
  for (const a of list) if (a.state !== "closed") out[a.severity] += 1;
  return out;
}

/** Strongest trigger value vs limit, e.g. "EWMA 1,34 > 1,00". */
export function evidenceText(t: AlertTrigger, unit: string | null | undefined): string {
  const stat = isStatisticalTrigger(t.source);
  const fmt = (v: number | null) => (stat ? formatStat(v) : t.source.startsWith("RUL") ? daysText(v) : formatSignal(v, unit));
  const cmp = t.source.startsWith("RUL") ? "≤" : ">";
  return t.value == null ? triggerLabel(t.source) : `${triggerLabel(t.source)} ${fmt(t.value)}${t.limit != null ? ` ${cmp} ${fmt(t.limit)}` : ""}`;
}

export function learningProgress(m: Pick<Monitor, "learning" | "state">): number | null {
  if (m.state !== "learning" || !m.learning || !m.learning.required) return null;
  return Math.min(1, m.learning.windows / m.learning.required);
}

/** Round axis bounds and ~4–6 ticks (1/2/5 × 10^k steps) so axes read 190, 200, 210… instead of 190,3. */
export function niceScale(lo: number, hi: number, target = 5): { domain: [number, number]; ticks: number[] } {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return { domain: [0, 1], ticks: [0, 1] };
  if (hi <= lo) { const d = Math.abs(lo) * 0.05 || 1; lo -= d; hi += d; }
  const raw = (hi - lo) / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => s >= raw) ?? 10 * mag;
  const a = Math.floor(lo / step) * step;
  const b = Math.ceil(hi / step) * step;
  const ticks: number[] = [];
  for (let v = a; v <= b + step / 2; v += step) ticks.push(Math.round(v / step) * step);
  return { domain: [a, b], ticks };
}

// ── chart data ────────────────────────────────────────────────────

export interface ChartRow {
  t: number;
  x: number | null;
  /** [low, high] of the active m0 ± 3σ band (null before the baseline). */
  band: [number, number] | null;
  m0: number | null;
  ewma: number | null;
  cusum: number | null;
  phase: string | null;
  anomaly: boolean;
  /** Projection of the fitted trend (P-5/P-6) from the last window to the failure limit. */
  proj: number | null;
}

/** A plant-local date → epoch ms at local noon (for placing the projected date on a time axis). */
export function dateToMs(d: string): number {
  const [y, m, dd] = d.split("-").map(Number);
  return new Date(y, (m ?? 1) - 1, dd ?? 1, 12).getTime();
}

export interface ChartModel {
  rows: ChartRow[];
  failureLimit: number | null;
  ruleLines: { value: number; label: string; severity: PredSeverity }[];
  ewmaLimit: number | null;
  cusumH: number | null;
  /** RUL band on the time axis at the failure limit. */
  rulBand: { from: number; to: number } | null;
  projectedAt: number | null;
  invalidCount: number;
  validCount: number;
}

/** Builds the condition chart model. Invalid windows are gaps (x = null), never zeros (P-1). */
export function buildChartModel(s: FeatureSeries, rul: ConditionRul | null | undefined): ChartModel {
  const bands = s.bands.slice().sort((a, b) => Date.parse(a.from_end_at ?? "") - Date.parse(b.from_end_at ?? ""));
  const bandAt = (t: number) => {
    let cur: (typeof bands)[number] | null = null;
    for (const b of bands) if (b.from_end_at && Date.parse(b.from_end_at) <= t) cur = b;
    return cur;
  };
  const rows: ChartRow[] = s.points.map((p) => {
    const t = Date.parse(p.end_at);
    const b = p.phase === "monitoring" ? bandAt(t) : null;
    return {
      t, x: p.valid ? p.x : null, band: b ? [b.low, b.high] : null, m0: b ? b.m0 : null,
      ewma: p.valid ? p.ewma : null, cusum: p.valid ? p.cusum : null, phase: p.phase, anomaly: !!p.anomaly, proj: null,
    };
  });
  const lf = rul?.failure_limit ?? s.limits.failure_limit ?? null;
  let rulBand: ChartModel["rulBand"] = null;
  let projectedAt: number | null = null;
  const lastValid = [...rows].reverse().find((r) => r.x != null);
  if (rul && lf != null && rul.projected_date && rul.x_fit != null && lastValid && rul.rul_days != null && rul.rul_days > 0) {
    projectedAt = dateToMs(rul.projected_date);
    if (projectedAt > lastValid.t) {
      lastValid.proj = rul.x_fit;
      rows.push({ t: projectedAt, x: null, band: null, m0: null, ewma: null, cusum: null, phase: "projection", anomaly: false, proj: lf });
    }
    if (rul.projected_date_low || rul.projected_date_high) {
      const from = rul.projected_date_low ? dateToMs(rul.projected_date_low) : projectedAt;
      const to = rul.projected_date_high ? dateToMs(rul.projected_date_high) : projectedAt;
      rulBand = { from, to };
    }
  }
  const m0 = s.baselines.length ? s.baselines[s.baselines.length - 1].m0 : null;
  const ruleLines = s.limits.rules.filter((r) => r.enabled && (r.type === "threshold" || (r.type === "pct_of_baseline" && m0 != null)))
    .map((r) => ({
      value: r.type === "threshold" ? r.value : r.value * (m0 as number),
      label: r.code,
      severity: r.severity,
    }));
  return {
    rows: rows.sort((a, b) => a.t - b.t), failureLimit: lf, ruleLines, ewmaLimit: s.limits.ewma_limit ?? null,
    cusumH: s.limits.cusum_h ?? null, rulBand, projectedAt,
    invalidCount: s.points.filter((p) => !p.valid).length, validCount: s.points.filter((p) => p.valid).length,
  };
}

/** Deep link to the alert detail / condition chart (matches ai_assistant.md catalog links). */
export function alertLink(id: string): string {
  return `/maintenance/predictive?alert=${encodeURIComponent(id)}`;
}
export function conditionLink(positionId: string, feature?: Feature | null): string {
  return `/maintenance/predictive?position_id=${encodeURIComponent(positionId)}${feature ? `&feature=${feature}` : ""}`;
}

/** P-9: compatible ISO 14224 modes per signal (for the defect_confirmed failure record). */
export const COMPATIBLE_MODES: Record<Feature, string[]> = {
  stroke_time: ["PDE", "INL", "ELP", "DOP"],
  current: ["OHE", "PDE", "BRD", "FTS"],
  vibration: ["VIB", "NOI", "BRD"],
  temperature: ["OHE", "BRD"],
};

export function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}
