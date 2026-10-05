import clsx from "clsx";
import type { ReactNode } from "react";

type Tone = "warning" | "danger";

interface Props {
  label: string;
  value: string | number;
  unit?: string;
  sub?: string;
  icon?: ReactNode;
  /** Everything is fine (e.g. all machines running). */
  highlight?: boolean;
  /** Needs attention. */
  tone?: Tone;
}

export default function StatCard({ label, value, unit, sub, icon, highlight, tone }: Props) {
  return (
    <div
      className={clsx(
        "rounded-xl border p-4 sm:p-5",
        tone === "danger"  ? "border-red-500/40 bg-red-500/10"
        : tone === "warning" ? "border-yellow-500/30 bg-yellow-500/5"
        : highlight ? "border-brand-500/40 bg-brand-500/10"
        : "border-gray-800 bg-gray-900"
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm text-gray-400">{label}</p>
        {icon && <span className={clsx("text-gray-500", highlight && "text-brand-500")}>{icon}</span>}
      </div>
      <div className="mt-2 flex items-end gap-1">
        <span
          className={clsx(
            "text-2xl font-bold tabular-nums sm:text-3xl",
            tone === "danger" ? "text-red-300" : tone === "warning" ? "text-yellow-300" : highlight ? "text-brand-300" : "text-gray-100"
          )}
        >
          {value}
        </span>
        {unit && <span className="mb-0.5 text-sm text-gray-500">{unit}</span>}
      </div>
      {sub && <p className="mt-1.5 text-xs text-gray-500">{sub}</p>}
    </div>
  );
}
