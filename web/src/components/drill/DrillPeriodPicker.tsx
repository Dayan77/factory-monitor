import { useSearchParams } from "react-router-dom";
import clsx from "clsx";
import { inputClass } from "@/components/ui/Field";
import { Tabs } from "@/components/ui/Tabs";
import { DRILL_PERIOD_LABELS, DRILL_PERIODS, parseDrill, type DrillPeriod } from "@/utils/drilldown";
import { localDate, zonedToUtc } from "@/utils/period";

function dayToIso(day: string, tz: string, endOfDay: boolean): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  if (!m) return null;
  const t = zonedToUtc(+m[1], +m[2], +m[3] + (endOfDay ? 1 : 0), 0, 0, tz);
  return new Date(t).toISOString();
}

/**
 * Period of the drill-down (sprint6 §2.2 rule 1), kept in the URL so every level, breadcrumb and back button shares
 * it. "Hoje/ontem" are productive days resolved by the server.
 */
export default function DrillPeriodPicker({ tz = "America/Sao_Paulo", label, id = "drill-period" }: { tz?: string; label?: string; id?: string }) {
  const [params, setParams] = useSearchParams();
  const ctx = parseDrill(params);

  function update(period: DrillPeriod, start?: string | null, stop?: string | null) {
    const next = new URLSearchParams(params);
    next.set("period", period);
    next.delete("from");
    next.delete("to");
    if (period === "custom" && start && stop) {
      next.set("start", start);
      next.set("stop", stop);
    } else if (period !== "custom") {
      next.delete("start");
      next.delete("stop");
    }
    setParams(next, { replace: true });
  }

  const startDay = ctx.start ? localDate(Date.parse(ctx.start), tz) : "";
  const stopDay = ctx.stop ? localDate(Date.parse(ctx.stop) - 1, tz) : "";
  const customOpen = params.get("period") === "custom";

  return (
    <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2" data-testid="drill-period">
      {label && <span className="sr-only">{label}</span>}
      <Tabs id={id} label="Período" size="sm" value={customOpen ? "custom" : ctx.period}
        onChange={(p) => {
          if (p === "custom") {
            const today = localDate(Date.now(), tz);
            const s = dayToIso(today, tz, false);
            const e = dayToIso(today, tz, true);
            const next = new URLSearchParams(params);
            next.set("period", "custom");
            if (!next.get("start") && s && e) { next.set("start", s); next.set("stop", e); }
            setParams(next, { replace: true });
          } else update(p);
        }}
        items={DRILL_PERIODS.map((p) => ({ value: p, label: DRILL_PERIOD_LABELS[p] }))} />
      {customOpen && (
        <div className="flex items-center gap-2">
          <label className="sr-only" htmlFor={`${id}-from`}>Data inicial</label>
          <input id={`${id}-from`} type="date" value={startDay} className={clsx(inputClass, "!w-auto min-h-9 py-1")}
            onChange={(e) => update("custom", dayToIso(e.target.value, tz, false), ctx.stop ?? dayToIso(e.target.value, tz, true))} />
          <span className="text-caption text-fg-subtle">até</span>
          <label className="sr-only" htmlFor={`${id}-to`}>Data final</label>
          <input id={`${id}-to`} type="date" value={stopDay} className={clsx(inputClass, "!w-auto min-h-9 py-1")}
            onChange={(e) => update("custom", ctx.start ?? dayToIso(e.target.value, tz, false), dayToIso(e.target.value, tz, true))} />
        </div>
      )}
    </div>
  );
}
