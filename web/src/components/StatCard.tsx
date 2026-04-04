import clsx from "clsx";
import type { ReactNode } from "react";

interface Props {
  label: string;
  value: string | number;
  unit?: string;
  sub?: string;
  trend?: "up" | "down" | "neutral";
  trendValue?: string;
  icon?: ReactNode;
  highlight?: boolean;
}

export default function StatCard({ label, value, unit, sub, trend, trendValue, icon, highlight }: Props) {
  return (
    <div
      className={clsx(
        "rounded-xl border p-5",
        highlight
          ? "border-brand-500/40 bg-brand-500/10"
          : "border-gray-800 bg-gray-900"
      )}
    >
      <div className="flex items-start justify-between">
        <p className="text-sm text-gray-400">{label}</p>
        {icon && (
          <span className={clsx("text-gray-500", highlight && "text-brand-500")}>
            {icon}
          </span>
        )}
      </div>
      <div className="mt-2 flex items-end gap-1">
        <span className={clsx("text-3xl font-bold", highlight ? "text-brand-300" : "text-gray-100")}>
          {value}
        </span>
        {unit && <span className="mb-0.5 text-sm text-gray-500">{unit}</span>}
      </div>
      {(sub || trendValue) && (
        <div className="mt-1.5 flex items-center gap-2">
          {trendValue && (
            <span
              className={clsx(
                "text-xs font-medium",
                trend === "up"   && "text-brand-400",
                trend === "down" && "text-red-400",
                trend === "neutral" && "text-gray-500"
              )}
            >
              {trend === "up" ? "▲" : trend === "down" ? "▼" : "—"} {trendValue}
            </span>
          )}
          {sub && <span className="text-xs text-gray-600">{sub}</span>}
        </div>
      )}
    </div>
  );
}
