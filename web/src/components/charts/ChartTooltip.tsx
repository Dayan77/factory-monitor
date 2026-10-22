import type { TooltipProps } from "recharts";
import { formatDateTime } from "@/utils/format";

/** Token-styled tooltip: values in text ink, a colored swatch carries series identity. */
export default function ChartTooltip({
  active, payload, label, format,
}: TooltipProps<number, string> & { format: (v: number) => string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-caption shadow-pop">
      <p className="mb-1 font-medium text-fg">{formatDateTime(String(label))}</p>
      {payload.map((p) => (
        <p key={String(p.dataKey)} className="flex items-center gap-2 text-fg-muted">
          <span aria-hidden="true" className="size-2 rounded-sm" style={{ background: p.color }} />
          <span>{p.name}</span>
          <span className="ml-auto pl-3 font-semibold tabular text-fg">{format(Number(p.value ?? 0))}</span>
        </p>
      ))}
    </div>
  );
}
