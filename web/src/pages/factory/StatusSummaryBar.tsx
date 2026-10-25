import clsx from "clsx";
import type { StatusSummary } from "@/services/types";

const SEGMENTS = [
  { key: "error", label: "Falha", fill: "bg-danger-solid" },
  { key: "maintenance", label: "Manutenção", fill: "bg-warn-solid" },
  { key: "stopped", label: "Parada", fill: "bg-gray-400" },
  { key: "unknown", label: "Sem comunicação", fill: "bg-gray-600" },
  { key: "running", label: "Operando", fill: "bg-ok-solid" },
] as const;

/** Stacked status bar + labelled counts (identity is never color-alone). */
export default function StatusSummaryBar({ summary, compact = false }: { summary: StatusSummary; compact?: boolean }) {
  const total = SEGMENTS.reduce((s, seg) => s + (summary[seg.key] ?? 0), 0);
  const visible = SEGMENTS.filter((seg) => (summary[seg.key] ?? 0) > 0);
  return (
    <div>
      <div className="flex h-2 w-full gap-0.5 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
        {visible.map((seg) => (
          <span
            key={seg.key}
            className={clsx("h-full transition-[flex-grow] duration-500 ease-out-soft first:rounded-l-full last:rounded-r-full", seg.fill)}
            style={{ flexGrow: summary[seg.key] ?? 0 }}
          />
        ))}
      </div>
      <ul className={clsx("mt-2 flex flex-wrap gap-x-3 gap-y-1 text-caption", compact && "gap-x-2")} aria-label="Status das máquinas">
        {total === 0 && <li className="text-fg-subtle">Nenhuma máquina</li>}
        {visible.map((seg) => (
          <li key={seg.key} data-status={seg.key} className="flex items-center gap-1.5 text-fg-muted">
            <span aria-hidden="true" className={clsx("size-2 rounded-full", seg.fill)} />
            <span className="font-semibold tabular text-fg">{summary[seg.key]}</span>
            {seg.label}
          </li>
        ))}
      </ul>
    </div>
  );
}
