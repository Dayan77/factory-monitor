import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import type { OeeGroupBy, Shift } from "@/services/types";
import { resolvePeriod, type PeriodPreset, type ResolvedPeriod } from "@/utils/period";

const PRESETS: PeriodPreset[] = ["shift", "today", "yesterday", "7d", "30d", "custom"];
const GROUPS: OeeGroupBy[] = ["hour", "shift", "day"];

/**
 * Period state kept in the URL (?period=&from=&to=&group=) so every view is linkable / drillable.
 * `nowBucket` keeps the resolved window stable between renders (re-resolved each minute).
 */
export function usePeriodParams(tz: string, shifts: Shift[] | undefined, defaultPreset: PeriodPreset = "today") {
  const [params, setParams] = useSearchParams();
  const raw = params.get("period") as PeriodPreset | null;
  const preset: PeriodPreset = raw && PRESETS.includes(raw) ? raw : defaultPreset;
  const from = params.get("from") ?? "";
  const to = params.get("to") ?? "";
  const groupRaw = params.get("group") as OeeGroupBy | null;
  const nowBucket = Math.floor(Date.now() / 60_000) * 60_000;

  const period: ResolvedPeriod = useMemo(
    () => resolvePeriod(preset, { now: nowBucket, tz, shifts, customFrom: from, customTo: to }),
    [preset, nowBucket, tz, shifts, from, to],
  );
  const groupBy: OeeGroupBy = groupRaw && GROUPS.includes(groupRaw) ? groupRaw : period.groupBy;

  function update(patch: Record<string, string | null>) {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v == null || v === "") next.delete(k);
      else next.set(k, v);
    }
    setParams(next, { replace: true });
  }

  return {
    params, update, preset, from, to, groupBy, period,
    setPreset: (p: PeriodPreset) => update({ period: p, group: null }),
    setCustom: (f: string, t: string) => update({ period: "custom", from: f, to: t, group: null }),
    setGroupBy: (g: OeeGroupBy) => update({ group: g }),
  };
}
