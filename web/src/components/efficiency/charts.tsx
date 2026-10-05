/**
 * Efficiency charts (Recharts). One y-axis per chart (never dual-axis); token colors so both themes
 * work; each chart sits in a ChartFrame with an accessible summary and a data table.
 */

import {
  Bar, BarChart, CartesianGrid, Cell, ComposedChart, LabelList, Legend, Line, LineChart, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipProps,
} from "recharts";
import ChartFrame from "@/components/charts/ChartFrame";
import type { OeePoint } from "@/services/types";
import { formatPct } from "@/utils/format";
import { formatDuration, formatPct1, formatShortDuration, type ParetoBar, type WaterfallStep } from "@/utils/efficiency";

const AXIS = { fill: "rgb(var(--c-fg-subtle))", fontSize: 11 };
const GRID = "rgb(var(--c-grid))";

export const KPI_SERIES = [
  { key: "oee", name: "OEE", color: "rgb(var(--c-kpi-oee))", dash: undefined, width: 3 },
  { key: "availability", name: "Disponibilidade", color: "rgb(var(--c-kpi-a))", dash: "6 3", width: 2 },
  { key: "performance", name: "Desempenho", color: "rgb(var(--c-kpi-p))", dash: "2 3", width: 2 },
  { key: "quality", name: "Qualidade", color: "rgb(var(--c-kpi-q))", dash: "9 3 2 3", width: 2 },
] as const;

function Box({ title, rows }: { title: string; rows: { color?: string; name: string; value: string }[] }) {
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-caption shadow-pop">
      <p className="mb-1 font-medium text-fg">{title}</p>
      {rows.map((r) => (
        <p key={r.name} className="flex items-center gap-2 text-fg-muted">
          {r.color && <span aria-hidden="true" className="size-2 rounded-sm" style={{ background: r.color }} />}
          <span>{r.name}</span>
          <span className="ml-auto pl-3 font-semibold tabular text-fg">{r.value}</span>
        </p>
      ))}
    </div>
  );
}

// ── trend ─────────────────────────────────────────────────────────

type TrendRow = { label: string; t: string } & Record<"oee" | "availability" | "performance" | "quality", number | null>;

function TrendTooltip({ active, payload, label }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  return (
    <Box
      title={String(label)}
      rows={payload.map((p) => ({ color: p.color, name: String(p.name), value: p.value == null ? "—" : `${Number(p.value).toFixed(1).replace(".", ",")}%` }))}
    />
  );
}

export function OeeTrendChart({ points, height = 280, labelOf }: { points: OeePoint[]; height?: number; labelOf?: (p: OeePoint) => string }) {
  const pct = (v: number | null) => (v == null || !Number.isFinite(v) ? null : v * 100);
  const data: TrendRow[] = points.map((p) => ({
    t: p.t, label: labelOf ? labelOf(p) : p.label, oee: pct(p.oee), availability: pct(p.availability), performance: pct(p.performance), quality: pct(p.quality),
  }));
  const max = Math.max(100, ...data.flatMap((d) => [d.performance ?? 0, d.availability ?? 0]));
  return (
    <ChartFrame
      label={`Tendência de OEE, disponibilidade, desempenho e qualidade em ${points.length} intervalos`}
      height={height}
      table={{
        headers: ["Intervalo", "OEE", "Disponib.", "Desempenho", "Qualidade"],
        rows: points.map((p) => [labelOf ? labelOf(p) : p.label, formatPct(p.oee), formatPct(p.availability), formatPct(p.performance), formatPct(p.quality)]),
      }}
    >
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -4 }}>
          <CartesianGrid vertical={false} stroke={GRID} />
          <XAxis dataKey="label" tick={AXIS} tickLine={false} axisLine={false} minTickGap={18} />
          <YAxis domain={[0, Math.ceil(max / 10) * 10]} tick={AXIS} tickLine={false} axisLine={false} width={48}
            tickFormatter={(v: number) => `${v}%`} />
          <ReferenceLine y={85} stroke="rgb(var(--c-fg-subtle))" strokeDasharray="3 3"
            label={{ value: "85%", position: "insideTopRight", fill: "rgb(var(--c-fg-subtle))", fontSize: 10 }} />
          <Tooltip content={<TrendTooltip />} cursor={{ stroke: "rgb(var(--c-fg-subtle))", strokeDasharray: "3 3" }} />
          <Legend iconType="plainline" iconSize={16} wrapperStyle={{ fontSize: 12, color: "rgb(var(--c-fg-muted))" }} />
          {KPI_SERIES.map((s) => (
            <Line key={s.key} type="monotone" dataKey={s.key} name={s.name} stroke={s.color} strokeWidth={s.width}
              strokeDasharray={s.dash} dot={data.length <= 2 ? { r: 4 } : false} activeDot={{ r: 4, strokeWidth: 2, stroke: "rgb(var(--c-surface))" }}
              connectNulls={false} isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// ── Pareto ────────────────────────────────────────────────────────

function ParetoTooltip({ active, payload }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  const b = payload[0].payload as ParetoBar;
  return (
    <Box title={b.label} rows={[
      { name: "Tempo perdido", value: formatDuration(b.duration_s) },
      { name: "Eventos", value: b.events ? String(b.events) : "—" },
      { name: "Participação", value: formatPct1(b.pct / 100) },
      { name: "Acumulado", value: formatPct1(b.cumulative / 100) },
    ]} />
  );
}

/**
 * Pareto of losses: bars = share of the total loss, line = cumulative share — both in % on ONE axis.
 * Minutes are in the tooltip and the table.
 */
export function ParetoChart({ bars, height = 300, onSelect }: { bars: ParetoBar[]; height?: number; onSelect?: (b: ParetoBar) => void }) {
  return (
    <ChartFrame
      label={`Pareto de perdas: ${bars.length} motivos; maior perda ${bars[0]?.label ?? "—"} com ${formatPct1((bars[0]?.pct ?? 0) / 100)}`}
      height={height}
      table={{
        headers: ["Motivo", "Tempo", "Eventos", "%", "% acumulado"],
        rows: bars.map((b) => [b.label, formatDuration(b.duration_s), b.events, formatPct1(b.pct / 100), formatPct1(b.cumulative / 100)]),
      }}
    >
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={bars} margin={{ top: 16, right: 8, bottom: 0, left: -4 }} barCategoryGap="18%">
          <CartesianGrid vertical={false} stroke={GRID} />
          <XAxis dataKey="label" tick={AXIS} tickLine={false} axisLine={false} interval={0} height={48}
            tickFormatter={(v: string) => (v.length > 16 ? `${v.slice(0, 15)}…` : v)} angle={-20} textAnchor="end" />
          <YAxis domain={[0, 100]} ticks={[0, 25, 50, 80, 100]} tick={AXIS} tickLine={false} axisLine={false} width={48}
            tickFormatter={(v: number) => `${v}%`} />
          <ReferenceLine y={80} stroke="rgb(var(--c-fg-subtle))" strokeDasharray="3 3" />
          <Tooltip content={<ParetoTooltip />} cursor={{ fill: "rgb(var(--c-fg) / 0.06)" }} />
          <Bar dataKey="pct" name="Participação" radius={[4, 4, 0, 0]} isAnimationActive={false}
            onClick={onSelect ? (d: unknown) => onSelect(d as ParetoBar) : undefined} cursor={onSelect ? "pointer" : undefined}>
            {bars.map((b) => (
              <Cell key={b.key} fill={b.pseudo ? "rgb(var(--c-kpi-p))" : "rgb(var(--c-kpi-a))"} />
            ))}
          </Bar>
          <Line dataKey="cumulative" name="Acumulado" type="monotone" stroke="rgb(var(--c-kpi-q))" strokeWidth={2}
            dot={{ r: 4, fill: "rgb(var(--c-kpi-q))", stroke: "rgb(var(--c-surface))", strokeWidth: 2 }} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// ── waterfall ─────────────────────────────────────────────────────

type WfRow = WaterfallStep & { base_h: number; value_h: number };

function WaterfallTooltip({ active, payload }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  const s = payload[0].payload as WfRow;
  return <Box title={s.label} rows={[{ name: s.kind === "loss" ? "Perda" : "Tempo", value: formatDuration(s.value_s) }, { name: s.hint, value: "" }]} />;
}

const WF_COLOR: Record<string, string> = {
  total: "rgb(var(--c-neutral-solid))",
  net: "rgb(var(--c-kpi-oee))",
  loss: "rgb(var(--c-kpi-q))",
  pdot: "rgb(var(--c-info-solid) / 0.55)",
  nodata: "rgb(var(--c-neutral-solid) / 0.45)",
};

/** Bar value label without Recharts' word wrapping ("8,2 h" must stay on one line). */
function WaterfallLabel(props: { x?: number | string; y?: number | string; width?: number | string; value?: number | string }) {
  const v = Number(props.value ?? 0);
  if (!(v > 0)) return null;
  const x = Number(props.x ?? 0) + Number(props.width ?? 0) / 2;
  return (
    <text x={x} y={Number(props.y ?? 0) - 6} textAnchor="middle" fontSize={10} fill="rgb(var(--c-fg-muted))">
      {formatShortDuration(v)}
    </text>
  );
}

/** Time waterfall POT → … → tempo produtivo líquido, in hours (floating loss bars). */
export function WaterfallChart({ steps, height = 280 }: { steps: WaterfallStep[]; height?: number }) {
  const data: WfRow[] = steps.map((s) => ({ ...s, base_h: s.base_s / 3600, value_h: s.value_s / 3600 }));
  const color = (s: WaterfallStep) => (s.key === "net" ? WF_COLOR.net : s.kind === "total" ? WF_COLOR.total : WF_COLOR[s.key] ?? WF_COLOR.loss);
  return (
    <ChartFrame
      label={`Cascata de tempo: ${steps.map((s) => `${s.label} ${formatDuration(s.value_s)}`).join(", ")}`}
      height={height}
      table={{ headers: ["Etapa", "Tempo", "Descrição"], rows: steps.map((s) => [s.label, formatDuration(s.value_s), s.hint]) }}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 24, right: 8, bottom: 0, left: -8 }} barCategoryGap="14%">
          <CartesianGrid vertical={false} stroke={GRID} />
          <XAxis dataKey="label" tick={AXIS} tickLine={false} axisLine={false} interval={0} height={40} angle={-20} textAnchor="end" />
          <YAxis tick={AXIS} tickLine={false} axisLine={false} width={44} tickFormatter={(v: number) => `${Math.round(v)} h`} />
          <Tooltip content={<WaterfallTooltip />} cursor={{ fill: "rgb(var(--c-fg) / 0.06)" }} />
          <Bar dataKey="base_h" stackId="w" fill="transparent" isAnimationActive={false} />
          <Bar dataKey="value_h" stackId="w" radius={[4, 4, 0, 0]} isAnimationActive={false}>
            {data.map((s) => <Cell key={s.key} fill={color(s)} />)}
            <LabelList dataKey="value_s" content={WaterfallLabel} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
