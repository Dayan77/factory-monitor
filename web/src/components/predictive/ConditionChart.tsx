/**
 * Condition chart (predictive.md P-12): the signal per window with the frozen baseline band m0 ± 3σ, the rule
 * limits and the failure limit, plus the RUL projection (fitted trend to the failure limit, with its date band).
 * EWMA and CUSUM go in two small charts below (each with its own limit) — never a second y-axis.
 */
import {
  Area, CartesianGrid, ComposedChart, Legend, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer,
  Tooltip, XAxis, YAxis, type TooltipProps,
} from "recharts";
import ChartFrame from "@/components/charts/ChartFrame";
import type { ConditionRul, FeatureSeries } from "@/services/predictiveTypes";
import { buildChartModel, formatSignal, formatStat, niceScale, type ChartRow } from "@/utils/predictive";
import { formatDate, formatDateTime } from "@/utils/format";

const AXIS = { fill: "rgb(var(--c-fg-subtle))", fontSize: 11 };
const GRID = "rgb(var(--c-grid))";
const C_SIGNAL = "rgb(var(--c-chart-3))";
const C_BAND = "rgb(var(--c-fg-subtle))";
const C_LIMIT = "rgb(var(--c-danger-solid))";
const C_RULE = "rgb(var(--c-warn-solid))";
const C_PROJ = "rgb(var(--c-fg-muted))";

function shortDate(t: number) {
  const d = new Date(t);
  return `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function SignalTooltip({ active, payload, unit }: TooltipProps<number, string> & { unit: string }) {
  if (!active || !payload?.length) return null;
  const r = payload[0].payload as ChartRow;
  const rows: { name: string; value: string; color?: string }[] = [];
  if (r.phase === "projection") rows.push({ name: "Projeção: limite de falha", value: formatSignal(r.proj, unit), color: C_PROJ });
  else {
    rows.push({ name: "Sinal", value: r.x == null ? "janela inválida" : formatSignal(r.x, unit), color: C_SIGNAL });
    if (r.m0 != null && r.band) rows.push({ name: "Referência ± 3σ", value: `${formatSignal(r.m0, unit)} (${formatSignal(r.band[0], null)}–${formatSignal(r.band[1], null)})`, color: C_BAND });
    if (r.ewma != null) rows.push({ name: "EWMA", value: formatStat(r.ewma) });
    if (r.cusum != null) rows.push({ name: "CUSUM", value: formatStat(r.cusum) });
    if (r.anomaly) rows.push({ name: "Anomalia pontual", value: "sim" });
    if (r.phase && r.phase !== "monitoring") rows.push({ name: "Fase", value: r.phase === "warmup" ? "aquecimento" : "linha de base" });
  }
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-caption shadow-pop">
      <p className="mb-1 font-medium text-fg">{r.phase === "projection" ? `${formatDate(new Date(r.t).toISOString())} (projetado)` : formatDateTime(new Date(r.t).toISOString())}</p>
      {rows.map((x) => (
        <p key={x.name} className="flex items-center gap-2 text-fg-muted">
          {x.color && <span aria-hidden="true" className="size-2 rounded-sm" style={{ background: x.color }} />}
          <span>{x.name}</span>
          <span className="ml-auto pl-3 font-semibold tabular text-fg">{x.value}</span>
        </p>
      ))}
    </div>
  );
}

function StatChart({ rows, dataKey, name, limit, height }: { rows: ChartRow[]; dataKey: "ewma" | "cusum"; name: string; limit: number | null; height: number }) {
  const data = rows.filter((r) => r.phase !== "projection");
  const vals = data.map((r) => r[dataKey]).filter((v): v is number => v != null);
  const scale = niceScale(Math.min(0, ...vals), Math.max(limit ?? 1, ...vals), 3);
  const fmtTick = (v: number) => new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 }).format(v);
  return (
    <div>
      <p className="mb-1 text-caption font-medium text-fg-muted">{name}{limit != null && <span className="text-fg-subtle"> · limite {formatStat(limit)}</span>}</p>
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
            <CartesianGrid vertical={false} stroke={GRID} />
            <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} tick={AXIS} tickLine={false} axisLine={false} tickFormatter={shortDate} minTickGap={28} />
            <YAxis domain={scale.domain} ticks={scale.ticks} tick={AXIS} tickLine={false} axisLine={false} width={44} tickFormatter={fmtTick} />
            {limit != null && <ReferenceLine y={limit} stroke={C_RULE} strokeDasharray="4 3" />}
            <Tooltip formatter={(v: number) => formatStat(v)} labelFormatter={(t: number) => formatDateTime(new Date(t).toISOString())}
              contentStyle={{ background: "rgb(var(--c-surface))", border: "1px solid rgb(var(--c-line))", borderRadius: 8, fontSize: 12 }} />
            <Line type="linear" dataKey={dataKey} name={name} stroke={C_SIGNAL} strokeWidth={2} dot={false} connectNulls={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export default function ConditionChart({ series, rul, height = 260, compact = false }: {
  series: FeatureSeries; rul?: ConditionRul | null; height?: number; compact?: boolean;
}) {
  const unit = series.unit;
  const m = buildChartModel(series, rul);
  const ys = m.rows.flatMap((r) => [r.x, r.proj, r.band?.[0], r.band?.[1]].filter((v): v is number => v != null));
  if (m.failureLimit != null) ys.push(m.failureLimit);
  m.ruleLines.forEach((l) => ys.push(l.value));
  const scale = niceScale(Math.min(...ys), Math.max(...ys));
  const tickFmt = (v: number) => new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 }).format(v);
  const showStats = !compact && m.rows.some((r) => r.ewma != null || r.cusum != null);
  const label = `${series.feature_label ?? series.feature} de ${series.position_name ?? "posição"}: ${m.validCount} janelas válidas`
    + (m.failureLimit != null ? `, limite de falha ${formatSignal(m.failureLimit, unit)}` : "")
    + (m.projectedAt ? `, falha projetada em ${formatDate(new Date(m.projectedAt).toISOString())}` : "");
  return (
    <div className="space-y-3" data-testid="condition-chart">
      <ChartFrame label={label} height={height} table={{
        headers: ["Fim da janela", "Valor", "Fase", "EWMA", "CUSUM"],
        rows: series.points.slice(-200).map((p) => [formatDateTime(p.end_at), p.valid ? formatSignal(p.x, unit) : "inválida", p.phase ?? "—", formatStat(p.ewma), formatStat(p.cusum)]),
      }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={m.rows} margin={{ top: 12, right: 12, bottom: 0, left: -4 }}>
            <CartesianGrid vertical={false} stroke={GRID} />
            <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} tick={AXIS} tickLine={false} axisLine={false} tickFormatter={shortDate} minTickGap={28} />
            <YAxis domain={scale.domain} ticks={scale.ticks} tick={AXIS} tickLine={false} axisLine={false} width={52} tickFormatter={tickFmt} />
            {m.rulBand && m.failureLimit != null && (
              <ReferenceArea x1={m.rulBand.from} x2={m.rulBand.to} fill={C_LIMIT} fillOpacity={0.08} stroke="none" ifOverflow="extendDomain"
                label={compact ? undefined : { value: "faixa da RUL", position: "insideTop", fill: "rgb(var(--c-fg-muted))", fontSize: 10 }} />
            )}
            <Area type="stepAfter" dataKey="band" name="Referência m0 ± 3σ" stroke="none" fill={C_BAND} fillOpacity={0.16} connectNulls={false} isAnimationActive={false} activeDot={false} />
            <Line type="stepAfter" dataKey="m0" name="Referência m0" stroke={C_BAND} strokeWidth={1} strokeDasharray="2 3" dot={false} connectNulls={false} isAnimationActive={false} legendType="none" />
            {m.ruleLines.map((r) => (
              <ReferenceLine key={r.label} y={r.value} stroke={r.severity === "critical" ? C_LIMIT : C_RULE} strokeDasharray="5 4"
                label={compact ? undefined : { value: r.label, position: "insideBottomLeft", fill: "rgb(var(--c-fg-muted))", fontSize: 10 }} />
            ))}
            {m.failureLimit != null && (
              <ReferenceLine y={m.failureLimit} stroke={C_LIMIT} strokeWidth={1.5}
                label={{ value: `limite de falha ${formatSignal(m.failureLimit, unit)}`, position: "insideTopLeft", fill: "rgb(var(--c-danger))", fontSize: 10 }} />
            )}
            <Line type="linear" dataKey="x" name={`${series.feature_label ?? "Sinal"} (${unit})`} stroke={C_SIGNAL} strokeWidth={2}
              dot={m.validCount <= 40 ? { r: 2.5, fill: C_SIGNAL, strokeWidth: 0 } : false} activeDot={{ r: 4, strokeWidth: 2, stroke: "rgb(var(--c-surface))" }}
              connectNulls={false} isAnimationActive={false} />
            <Line type="linear" dataKey="proj" name="Projeção da tendência" stroke={C_PROJ} strokeWidth={2} strokeDasharray="6 4" dot={false} connectNulls isAnimationActive={false} />
            <Tooltip content={<SignalTooltip unit={unit} />} cursor={{ stroke: "rgb(var(--c-fg-subtle))", strokeDasharray: "3 3" }} />
            {!compact && <Legend iconType="plainline" iconSize={16} wrapperStyle={{ fontSize: 12, color: "rgb(var(--c-fg-muted))" }} />}
          </ComposedChart>
        </ResponsiveContainer>
      </ChartFrame>
      {m.invalidCount > 0 && (
        <p className="text-caption text-fg-subtle">{m.invalidCount} janela(s) inválida(s) (máquina parada, feriado ou sem dados) aparecem como lacuna — não contam para nenhuma regra.</p>
      )}
      {showStats && (
        <div className="grid gap-3 sm:grid-cols-2">
          <StatChart rows={m.rows} dataKey="ewma" name="EWMA (em σ)" limit={m.ewmaLimit} height={120} />
          <StatChart rows={m.rows} dataKey="cusum" name="CUSUM (em σ)" limit={m.cusumH} height={120} />
        </div>
      )}
    </div>
  );
}
