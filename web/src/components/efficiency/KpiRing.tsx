import clsx from "clsx";
import { formatPct } from "@/utils/format";
import { oeeTone, TONE_FILL, TONE_TEXT } from "@/utils/efficiency";

interface Props {
  /** Ratio 0..1 (may exceed 1) or null when undefined. */
  value: number | null | undefined;
  label: string;
  /** Diameter in px. */
  size?: number;
  stroke?: number;
  className?: string;
  /** Font size class of the center number. */
  valueClassName?: string;
  sublabel?: string;
}

/**
 * Ring gauge (270° arc) for OEE-type ratios. Null shows "—" with an empty track — never a fake 0.
 * Color band follows oeeTone; the number is always printed, so color is never the only cue.
 */
export default function KpiRing({ value, label, size = 120, stroke, className, valueClassName, sublabel }: Props) {
  const sw = stroke ?? Math.max(6, Math.round(size / 11));
  const r = (size - sw) / 2;
  const circ = 2 * Math.PI * r;
  const arc = circ * 0.75;
  const v = value == null || !Number.isFinite(value) ? null : value;
  const filled = v == null ? 0 : arc * Math.min(1, Math.max(0, v));
  const tone = oeeTone(v);
  return (
    <div
      role="img"
      aria-label={`${label}: ${v == null ? "sem dado" : formatPct(v)}`}
      className={clsx("relative inline-flex aspect-square max-w-full shrink-0 items-center justify-center", className)}
      style={{ width: size }}
    >
      <svg width="100%" height="100%" viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        {/* rotate inside the SVG: a CSS transform on the element would enlarge its scrollable overflow */}
        <g transform={`rotate(135 ${size / 2} ${size / 2})`}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgb(var(--c-surface-2))" strokeWidth={sw}
          strokeDasharray={`${arc} ${circ}`} strokeLinecap="round" />
        {v != null && (
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={TONE_FILL[tone]} strokeWidth={sw}
            strokeDasharray={`${filled} ${circ}`} strokeLinecap="round"
            style={{ transition: "stroke-dasharray 600ms cubic-bezier(0.22, 1, 0.36, 1)" }} />
        )}
        </g>
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center pt-1">
        <span className={clsx("font-semibold leading-none tabular", TONE_TEXT[tone], valueClassName ?? "text-display")}>
          {v == null ? "—" : formatPct(v)}
        </span>
        <span className="mt-1 text-overline uppercase text-fg-subtle">{sublabel ?? label}</span>
      </div>
    </div>
  );
}
