import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { UsageMeasure, UsagePoint } from "@/services/types";
import { MEASURE_LABEL } from "@/utils/assets";
import { formatDateTime, formatHourLabel, formatMeasure } from "@/utils/format";
import ChartFrame from "./ChartFrame";
import ChartTooltip from "./ChartTooltip";

const AXIS = { fill: "rgb(var(--c-fg-subtle))", fontSize: 11 };
// same identity colors as the production chart: good = chart-1, scrap = chart-2; usage measures = chart-3
const COLOR: Partial<Record<UsageMeasure, string>> = { good: "rgb(var(--c-chart-1))", scrap: "rgb(var(--c-chart-2))" };

/** Usage per hourly bucket for one measure (single series: the card title names it). */
export default function UsageChart({ points, measure, height = 240 }: { points: UsagePoint[]; measure: UsageMeasure; height?: number }) {
  const fmt = (v: number) => formatMeasure(measure, v);
  const sum = points.reduce((s, p) => s + p.value, 0);
  return (
    <ChartFrame
      label={`${MEASURE_LABEL[measure]} por hora: total ${fmt(sum)} no período`}
      height={height}
      table={{ headers: ["Hora", MEASURE_LABEL[measure]], rows: points.map((p) => [formatDateTime(p.t), fmt(p.value)]) }}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={points} margin={{ top: 8, right: 4, bottom: 0, left: -12 }} barCategoryGap={2}>
          <CartesianGrid vertical={false} stroke="rgb(var(--c-grid))" />
          <XAxis dataKey="t" tickFormatter={formatHourLabel} tick={AXIS} tickLine={false} axisLine={false} minTickGap={16} />
          <YAxis tick={AXIS} tickLine={false} axisLine={false} width={52} tickFormatter={(v: number) => fmt(v)} />
          <Tooltip cursor={{ fill: "rgb(var(--c-fg) / 0.06)" }} content={<ChartTooltip format={fmt} />} />
          <Bar dataKey="value" name={MEASURE_LABEL[measure]} fill={COLOR[measure] ?? "rgb(var(--c-chart-3))"} radius={[3, 3, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
