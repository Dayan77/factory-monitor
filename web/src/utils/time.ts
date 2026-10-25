/**
 * "visto há X" formatting for machine/sensor freshness.
 * Pure functions so they can be unit-tested with a fixed `now`.
 */

/** Seconds elapsed between an ISO timestamp and `now` (ms). Null when unknown/invalid. */
export function secondsSince(iso: string | null | undefined, now: number): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.floor((now - t) / 1000));
}

/** "há 12 s", "há 3 min", "há 2 h", "há 4 d". */
export function formatAgo(seconds: number): string {
  if (seconds < 60) return `há ${seconds} s`;
  const min = Math.floor(seconds / 60);
  if (min < 60) return `há ${min} min`;
  const h = Math.floor(min / 60);
  if (h < 24) return `há ${h} h`;
  return `há ${Math.floor(h / 24)} d`;
}

/** Label for a machine's last_seen_at: "visto há 12 s" or "nunca visto". */
export function formatSeenAgo(iso: string | null | undefined, now: number): string {
  const s = secondsSince(iso, now);
  return s == null ? "nunca visto" : `visto ${formatAgo(s)}`;
}
