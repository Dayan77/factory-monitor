import clsx from "clsx";
import { formatPct } from "@/utils/format";

/**
 * Life consumed is a fraction (0..1, may exceed 1 — never truncated; docs/kpis.md §4.1).
 * Bands: green < 0.70, amber 0.70–0.90, red > 0.90.
 */
export type LifeBand = "ok" | "warn" | "danger" | "none";

export function lifeBand(fraction: number | null | undefined): LifeBand {
  if (fraction == null || Number.isNaN(fraction)) return "none";
  if (fraction > 0.9) return "danger";
  if (fraction >= 0.7) return "warn";
  return "ok";
}

const FILL: Record<LifeBand, string> = {
  ok: "bg-ok-solid",
  warn: "bg-warn-solid",
  danger: "bg-danger-solid",
  none: "bg-neutral-solid",
};

const TEXT: Record<LifeBand, string> = {
  ok: "text-fg",
  warn: "text-warn",
  danger: "text-danger",
  none: "text-fg-subtle",
};

interface ProgressProps {
  /** 0..1 */
  value: number;
  label: string;
  className?: string;
  fillClassName?: string;
  size?: "sm" | "md";
}

/** Accessible horizontal progress bar (role=progressbar). Values > 1 render full. */
export function Progress({ value, label, className, fillClassName = "bg-accent", size = "md" }: ProgressProps) {
  const clamped = Math.max(0, Math.min(1, value));
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(value * 100)}
      className={clsx("w-full overflow-hidden rounded-full bg-surface-2", size === "sm" ? "h-1.5" : "h-2", className)}
    >
      <div
        className={clsx("h-full rounded-full transition-[width] duration-500 ease-out-soft", fillClassName)}
        style={{ width: `${clamped * 100}%` }}
      />
    </div>
  );
}

interface LifeBarProps {
  fraction: number | null | undefined;
  /** Accessible name, e.g. "Vida consumida — Cilindro de fixação". */
  label?: string;
  /** Measure that determined the value (cycles/hours/starts). */
  measure?: string | null;
  showValue?: boolean;
  size?: "sm" | "md";
  className?: string;
}

const MEASURE_LABEL: Record<string, string> = { cycles: "ciclos", hours: "horas", starts: "partidas" };

export default function LifeBar({ fraction, label = "Vida consumida", measure, showValue = true, size = "md", className }: LifeBarProps) {
  const band = lifeBand(fraction);
  if (band === "none" || fraction == null) {
    return (
      <span data-band="none" className={clsx("text-caption text-fg-subtle", className)}>
        Sem vida nominal
      </span>
    );
  }
  return (
    <div data-band={band} className={clsx("flex min-w-[7rem] items-center gap-2", className)}>
      <Progress value={fraction} label={label} fillClassName={FILL[band]} size={size} className="flex-1" />
      {showValue && (
        <span
          className={clsx("w-12 shrink-0 text-right text-caption font-semibold tabular", TEXT[band])}
          title={measure ? `Medida determinante: ${MEASURE_LABEL[measure] ?? measure}` : undefined}
        >
          {formatPct(fraction)}
        </span>
      )}
    </div>
  );
}
