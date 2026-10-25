import type { ReactNode } from "react";
import clsx from "clsx";
import type { Tone } from "./Badge";
import KpiInfo from "./KpiInfo";
import type { KpiId } from "@/utils/kpiGlossary";

interface Props {
  label: ReactNode;
  value: ReactNode;
  unit?: ReactNode;
  hint?: ReactNode;
  icon?: ReactNode;
  tone?: Exclude<Tone, "accent" | "info">;
  className?: string;
  /** Changing key re-runs a subtle flash (live updates). */
  flashKey?: string | number;
  /** KPI with a definition in the glossary: shows the ⓘ with the explanation and the manual link. */
  kpi?: KpiId;
}

const VALUE_TONE = {
  neutral: "text-fg",
  ok: "text-ok",
  warn: "text-warn",
  danger: "text-danger",
} as const;

/** KPI tile: label, big tabular number, optional unit and hint. */
export default function Stat({ label, value, unit, hint, icon, tone = "neutral", className, flashKey, kpi }: Props) {
  return (
    <div className={clsx("surface rounded-xl border border-line bg-surface p-4 shadow-card", className)}>
      <div className="flex items-start justify-between gap-2">
        <p className="flex items-center gap-1.5 text-caption font-medium text-fg-muted">{label}{kpi && <KpiInfo kpi={kpi} />}</p>
        {icon && <span aria-hidden="true" className="text-fg-subtle">{icon}</span>}
      </div>
      <p className="mt-1.5 flex items-baseline gap-1">
        <span
          key={flashKey}
          className={clsx("rounded tabular text-display", VALUE_TONE[tone], flashKey !== undefined && "animate-flash")}
        >
          {value}
        </span>
        {unit && <span className="text-body text-fg-subtle">{unit}</span>}
      </p>
      {hint && <p className="mt-1 text-caption text-fg-muted">{hint}</p>}
    </div>
  );
}
