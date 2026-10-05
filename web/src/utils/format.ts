/** pt-BR number/date formatting used across asset & usage screens. */

const int = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const dec1 = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const compact = new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 });

/** 1234567 → "1.234.567" */
export function formatInt(n: number | null | undefined): string {
  return n == null || Number.isNaN(n) ? "—" : int.format(Math.round(n));
}

/** 12.345 → "12,3" */
export function formatDec1(n: number | null | undefined): string {
  return n == null || Number.isNaN(n) ? "—" : dec1.format(n);
}

/** 1234567 → "1,2 mi" (for tight spaces; full value goes in a title). */
export function formatCompact(n: number | null | undefined): string {
  return n == null || Number.isNaN(n) ? "—" : compact.format(n);
}

/** Fraction → percent: 0.724 → "72%", 1.05 → "105%" (never truncated at 100). */
export function formatPct(fraction: number | null | undefined): string {
  if (fraction == null || Number.isNaN(fraction)) return "—";
  return `${int.format(Math.round(fraction * 100))}%`;
}

/** Usage measure value with its unit. */
export function formatMeasure(measure: string, value: number | null | undefined): string {
  if (measure === "hours") return `${formatDec1(value)} h`;
  return formatInt(value);
}

/** ISO date/datetime → "05/10/2026". Date-only strings are not shifted by the timezone. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.exec(iso);
  if (dateOnly) {
    const [y, m, d] = iso.split("-");
    return `${d}/${m}/${y}`;
  }
  const t = new Date(iso);
  return Number.isNaN(t.getTime()) ? "—" : t.toLocaleDateString("pt-BR");
}

/** ISO → "05/10/2026 14:32" */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = new Date(iso);
  return Number.isNaN(t.getTime())
    ? "—"
    : t.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** ISO → "14h" label for hourly chart buckets. */
export function formatHourLabel(iso: string): string {
  const t = new Date(iso);
  return Number.isNaN(t.getTime()) ? "" : `${String(t.getHours()).padStart(2, "0")}h`;
}
