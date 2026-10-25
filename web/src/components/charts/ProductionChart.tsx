import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ProductionPoint } from "@/services/types";
import { formatDateTime, formatHourLabel, formatInt } from "@/utils/format";
import ChartFrame from "./ChartFrame";
import ChartTooltip from "./ChartTooltip";

const GOOD = "rgb(var(--c-chart-1))";
const SCRAP = "rgb(var(--c-chart-2))";
const AXIS = { fill: "rgb(var(--c-fg-subtle))", fontSize: 11 };

/**
 * Parts per hour (= cadence) as stacked bars: good + scrap. With 1 h buckets the bar height is
 * the hourly cadence in parts/h.
 */
export default function ProductionChart({ points, height = 260 }: { points: ProductionPoint[]; height?: number }) {
  const total = points.reduce((s, p) => s + p.good + p.scrap, 0);
  return (
    <ChartFrame
      label={`Produção por hora: ${points.length} horas, ${formatInt(total)} peças no período`}
      height={height}
      table={{
        headers: ["Hora", "Boas", "Refugo", "Peças/h"],
        rows: points.map((p) => [formatDateTime(p.t), formatInt(p.good), formatInt(p.scrap), formatInt(p.good + p.scrap)]),
      }}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={points} margin={{ top: 8, right: 4, bottom: 0, left: -12 }} barCategoryGap={2}>
          <CartesianGrid vertical={false} stroke="rgb(var(--c-grid))" />
          <XAxis dataKey="t" tickFormatter={formatHourLabel} tick={AXIS} tickLine={false} axisLine={false} minTickGap={16} />
          <YAxis tick={AXIS} tickLine={false} axisLine={false} allowDecimals={false} width={48} tickFormatter={(v: number) => formatInt(v)} />
          <Tooltip cursor={{ fill: "rgb(var(--c-fg) / 0.06)" }} content={<ChartTooltip format={formatInt} />} />
          <Legend iconType="square" iconSize={10} wrapperStyle={{ fontSize: 12, color: "rgb(var(--c-fg-muted))" }} />
          <Bar dataKey="good" name="Boas" stackId="p" fill={GOOD} stroke="rgb(var(--c-surface))" strokeWidth={1} isAnimationActive={false} />
          <Bar dataKey="scrap" name="Refugo" stackId="p" fill={SCRAP} stroke="rgb(var(--c-surface))" strokeWidth={1} radius={[3, 3, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
