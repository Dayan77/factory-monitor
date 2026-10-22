/**
 * The usage/production APIs return sparse hourly buckets (hours without usage are omitted).
 * For an honest time axis the gaps are filled with zero-valued buckets.
 */

const HOUR = 3600_000;

export function fillHourly<T extends { t: string }>(
  points: T[],
  start: string | undefined,
  stop: string | undefined,
  zero: (t: string) => T,
): T[] {
  if (!start || !stop) return points;
  const s = Date.parse(start);
  const e = Date.parse(stop);
  if (Number.isNaN(s) || Number.isNaN(e) || e <= s || (e - s) / HOUR > 24 * 31) return points;
  const byHour = new Map<number, T>();
  for (const p of points) {
    const t = Date.parse(p.t);
    if (!Number.isNaN(t)) byHour.set(Math.floor(t / HOUR) * HOUR, p);
  }
  const out: T[] = [];
  for (let h = Math.floor(s / HOUR) * HOUR; h < e; h += HOUR) {
    out.push(byHour.get(h) ?? zero(new Date(h).toISOString()));
  }
  // keep any point that falls outside the computed range (never drop server data)
  const covered = new Set(out.map((p) => p.t));
  for (const p of points) if (!covered.has(p.t) && !out.includes(p)) out.push(p);
  return out.sort((a, b) => Date.parse(a.t) - Date.parse(b.t));
}
