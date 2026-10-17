/**
 * Period presets for the efficiency screens, resolved in the PLANT's timezone (not the browser's):
 * "hoje" = local midnight → now, "turno atual" = the shift instance containing now, etc.
 */

import type { OeeGroupBy, Shift } from "@/services/types";

export type PeriodPreset = "shift" | "today" | "yesterday" | "7d" | "30d" | "custom";

export const PERIOD_LABELS: Record<PeriodPreset, string> = {
  shift: "Turno atual",
  today: "Hoje",
  yesterday: "Ontem",
  "7d": "7 dias",
  "30d": "30 dias",
  custom: "Personalizado",
};

export interface ResolvedPeriod {
  start: string;
  stop: string;
  /** Sensible trend granularity for the window. */
  groupBy: OeeGroupBy;
  /** Human label, e.g. "Turno A · 06:00–14:00". */
  label: string;
  /** Set when the preset can't be resolved (e.g. no shift running now). */
  unavailable?: string;
}

interface LocalParts { y: number; m: number; d: number; hh: number; mm: number; weekday: number }

const WEEKDAY_IDX: Record<string, number> = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 };

/** Wall-clock parts of instant `ms` in `tz` (weekday 0 = Monday). */
export function localParts(ms: number, tz: string): LocalParts {
  const f = new Intl.DateTimeFormat("en-US", {
    timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    weekday: "short", hourCycle: "h23",
  });
  const p: Record<string, string> = {};
  for (const part of f.formatToParts(new Date(ms))) p[part.type] = part.value;
  return { y: +p.year, m: +p.month, d: +p.day, hh: +p.hour % 24, mm: +p.minute, weekday: WEEKDAY_IDX[p.weekday] ?? 0 };
}

/** UTC instant of local wall time y-m-d hh:mm in `tz` (handles DST by a two-pass offset fix). */
export function zonedToUtc(y: number, m: number, d: number, hh: number, mm: number, tz: string): number {
  const guess = Date.UTC(y, m - 1, d, hh, mm);
  let t = guess;
  for (let i = 0; i < 2; i++) {
    const p = localParts(t, tz);
    const asUtc = Date.UTC(p.y, p.m - 1, p.d, p.hh, p.mm);
    t += guess - asUtc;
  }
  return t;
}

function addDays(y: number, m: number, d: number, n: number): [number, number, number] {
  const t = new Date(Date.UTC(y, m - 1, d + n));
  return [t.getUTCFullYear(), t.getUTCMonth() + 1, t.getUTCDate()];
}

function hm(s: string): [number, number] {
  const [h, m] = s.split(":").map(Number);
  return [h || 0, m || 0];
}

/** YYYY-MM-DD of instant `ms` in `tz`. */
export function localDate(ms: number, tz: string): string {
  const p = localParts(ms, tz);
  return `${p.y}-${String(p.m).padStart(2, "0")}-${String(p.d).padStart(2, "0")}`;
}

export interface ShiftInstance { shift: Shift; start: number; end: number; productiveDay: string }

/**
 * The active shift instance containing `now` (a shift that crosses midnight belongs to the
 * productive day it started on, matching the server's calendar).
 */
export function currentShiftInstance(shifts: Shift[], now: number, tz: string): ShiftInstance | null {
  const today = localParts(now, tz);
  for (const back of [0, -1]) {
    const [y, m, d] = addDays(today.y, today.m, today.d, back);
    const weekday = (today.weekday + back + 7) % 7;
    for (const s of shifts) {
      if (!s.is_active || !s.weekdays.includes(weekday)) continue;
      const [sh, sm] = hm(s.start_local);
      const [eh, em] = hm(s.end_local);
      const start = zonedToUtc(y, m, d, sh, sm, tz);
      const crosses = eh * 60 + em <= sh * 60 + sm;
      const [ey, emo, ed] = crosses ? addDays(y, m, d, 1) : [y, m, d];
      const end = zonedToUtc(ey, emo, ed, eh, em, tz);
      if (start <= now && now < end) {
        return { shift: s, start, end, productiveDay: `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}` };
      }
    }
  }
  return null;
}

export function resolvePeriod(
  preset: PeriodPreset,
  opts: { now: number; tz: string; shifts?: Shift[]; customFrom?: string; customTo?: string },
): ResolvedPeriod {
  const { now, tz } = opts;
  const t = localParts(now, tz);
  const midnight = (offsetDays: number) => {
    const [y, m, d] = addDays(t.y, t.m, t.d, offsetDays);
    return zonedToUtc(y, m, d, 0, 0, tz);
  };
  const iso = (ms: number) => new Date(ms).toISOString();
  // Open-ended windows ("até agora") must never be empty: in the first minute after midnight or a shift start
  // `now` (floored to the minute) equals `start` and the API rejects start == stop with 422.
  const MIN_WINDOW_MS = 60_000;
  const openStop = (start: number, cap = Infinity) => Math.min(Math.max(now, start + MIN_WINDOW_MS), cap);
  switch (preset) {
    case "shift": {
      const inst = currentShiftInstance(opts.shifts ?? [], now, tz);
      if (!inst) {
        return {
          start: iso(midnight(0)), stop: iso(openStop(midnight(0))), groupBy: "hour", label: "Nenhum turno em andamento",
          unavailable: opts.shifts && opts.shifts.length ? "Nenhum turno em andamento agora." : "Nenhum turno cadastrado.",
        };
      }
      return {
        start: iso(inst.start), stop: iso(openStop(inst.start, inst.end)), groupBy: "hour",
        label: `${inst.shift.name} · ${inst.shift.start_local}–${inst.shift.end_local}`,
      };
    }
    case "today":
      return { start: iso(midnight(0)), stop: iso(openStop(midnight(0))), groupBy: "hour", label: "Hoje" };
    case "yesterday":
      return { start: iso(midnight(-1)), stop: iso(midnight(0)), groupBy: "hour", label: "Ontem" };
    case "7d":
      return { start: iso(midnight(-6)), stop: iso(now), groupBy: "day", label: "Últimos 7 dias" };
    case "30d":
      return { start: iso(midnight(-29)), stop: iso(now), groupBy: "day", label: "Últimos 30 dias" };
    case "custom": {
      const parse = (s: string | undefined) => (s && /^\d{4}-\d{2}-\d{2}$/.test(s) ? s.split("-").map(Number) : null);
      const from = parse(opts.customFrom);
      const to = parse(opts.customTo) ?? from;
      if (!from || !to) {
        return { start: iso(midnight(0)), stop: iso(openStop(midnight(0))), groupBy: "hour", label: "Personalizado", unavailable: "Escolha as datas." };
      }
      const start = zonedToUtc(from[0], from[1], from[2], 0, 0, tz);
      const [ey, em, ed] = addDays(to[0], to[1], to[2], 1);
      const stop = Math.min(zonedToUtc(ey, em, ed, 0, 0, tz), now);
      if (stop <= start) {
        return { start: iso(start), stop: iso(start), groupBy: "hour", label: "Personalizado", unavailable: "A data final deve ser depois da inicial (e não no futuro)." };
      }
      const days = (stop - start) / 86_400_000;
      return { start: iso(start), stop: iso(stop), groupBy: days <= 2 ? "hour" : "day", label: "Personalizado" };
    }
  }
}
