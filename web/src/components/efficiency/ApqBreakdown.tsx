import clsx from "clsx";
import { formatPct } from "@/utils/format";
import { oeeTone, TONE_FILL } from "@/utils/efficiency";

const ITEMS = [
  { key: "availability", short: "D", label: "Disponibilidade" },
  { key: "performance", short: "P", label: "Desempenho" },
  { key: "quality", short: "Q", label: "Qualidade" },
] as const;

interface Props {
  availability: number | null | undefined;
  performance: number | null | undefined;
  quality: number | null | undefined;
  className?: string;
  /** Larger text for TV mode. */
  size?: "md" | "lg";
}

/** A × P × Q as three labelled bars (the weakest factor is the one to attack). */
export default function ApqBreakdown({ className, size = "md", ...values }: Props) {
  return (
    <dl className={clsx("space-y-2", className)}>
      {ITEMS.map((it) => {
        const v = values[it.key];
        const has = v != null && Number.isFinite(v);
        return (
          <div key={it.key} className="grid grid-cols-[minmax(0,7.5rem)_1fr_3rem] items-center gap-2">
            <dt className={clsx("truncate text-fg-muted", size === "lg" ? "text-heading" : "text-caption")}>{it.label}</dt>
            <dd className="h-2 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
              {has && (
                <div className="h-full rounded-full transition-[width] duration-500 ease-out-soft"
                  style={{ width: `${Math.min(100, Math.max(0, (v as number) * 100))}%`, background: TONE_FILL[oeeTone(v)] }} />
              )}
            </dd>
            <dd className={clsx("text-right font-semibold tabular text-fg", size === "lg" ? "text-title" : "text-caption")}>
              {has ? formatPct(v) : "—"}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
