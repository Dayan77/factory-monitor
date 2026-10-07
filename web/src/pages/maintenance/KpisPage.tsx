import { useMemo } from "react";
import KpiInfo from "@/components/ui/KpiInfo";
import type { KpiId } from "@/utils/kpiGlossary";
import { Link } from "react-router-dom";
import clsx from "clsx";
import ExportMenu from "@/components/reports/ExportMenu";
import { Card, CardHeader } from "@/components/ui/Card";
import ErrorState from "@/components/ui/ErrorState";
import { inputClass } from "@/components/ui/Field";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { useMaintenanceKpis } from "@/hooks/useMaintenance";
import { usePlant } from "@/hooks/useEfficiency";
import { formatDec1, formatDec2, formatPct } from "@/utils/format";
import { zonedToUtc } from "@/utils/period";
import { EXCLUDED_REASON_LABEL, isoDay } from "@/utils/maintenance";
import { useUrlFilters } from "./filters";

/** "2026-11" → [start, stop) as UTC ISO for the plant time zone. */
export function monthRange(month: string, tz: string): { start: string; stop: string; from: string; to: string } {
  const [y, m] = month.split("-").map(Number);
  const ny = m === 12 ? y + 1 : y;
  const nm = m === 12 ? 1 : m + 1;
  const lastDay = new Date(Date.UTC(ny, nm - 1, 0)).getUTCDate();
  return {
    start: new Date(zonedToUtc(y, m, 1, 0, 0, tz)).toISOString(),
    stop: new Date(zonedToUtc(ny, nm, 1, 0, 0, tz)).toISOString(),
    from: `${month}-01`,
    to: `${month}-${String(lastDay).padStart(2, "0")}`,
  };
}

export function previousMonth(today: string): string {
  const [y, m] = today.split("-").map(Number);
  return m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, "0")}`;
}

function KpiTile({ label, value, hint, tone = "neutral", to, testId, kpi }: {
  label: string; value: string; hint?: React.ReactNode; tone?: "ok" | "warn" | "danger" | "neutral"; to?: string; testId?: string; kpi?: KpiId;
}) {
  const body = (
    <>
      <p className={clsx("text-caption font-medium text-fg-muted", kpi && "pr-8")}>{label}</p>
      <p data-testid={testId} className={clsx("mt-1.5 text-display tabular", { ok: "text-ok", warn: "text-warn", danger: "text-danger", neutral: "text-fg" }[tone])}>{value}</p>
      {hint && <div className="mt-1 text-caption text-fg-subtle">{hint}</div>}
      {to && <p className="mt-2 text-caption font-medium text-ok">Ver as OS ›</p>}
    </>
  );
  const cls = "surface block rounded-xl border border-line bg-surface p-4 shadow-card";
  const tile = to ? <Link to={to} className={clsx(cls, "h-full transition-colors hover:border-line-strong")}>{body}</Link> : <div className={clsx(cls, "h-full")}>{body}</div>;
  // the ⓘ is a sibling of the link (never a button inside a link)
  return kpi ? <div className="relative">{tile}<KpiInfo kpi={kpi} className="absolute right-4 top-4" /></div> : tile;
}

/** Backlog in weeks against the 2–4 week reference band (not a chart: one number + its band). */
function BacklogBand({ weeks }: { weeks: number }) {
  const max = 6;
  const pos = Math.min(weeks, max) / max;
  return (
    <div className="mt-3" aria-hidden="true">
      <div className="relative h-2 rounded-full bg-surface-2">
        <div className="absolute inset-y-0 rounded-full bg-ok-solid/30" style={{ left: `${(2 / max) * 100}%`, width: `${(2 / max) * 100}%` }} />
        <div className="absolute -top-1 h-4 w-1 rounded-full bg-fg" style={{ left: `calc(${pos * 100}% - 2px)` }} />
      </div>
      <div className="mt-1 flex justify-between text-[0.6875rem] text-fg-subtle"><span>0</span><span>2</span><span>4</span><span>6+ sem.</span></div>
    </div>
  );
}

/** Manager/planner: PMP, schedule compliance (with exclusions), backlog (workflows §2 "Gestão da manutenção"). */
export default function KpisPage() {
  const { values: f, set } = useUrlFilters(["month"] as const);
  const { plant, tz } = usePlant();
  const today = isoDay(Date.now(), tz);
  const month = /^\d{4}-\d{2}$/.test(f.month) ? f.month : previousMonth(today);
  const range = useMemo(() => monthRange(month, tz), [month, tz]);
  const kpis = useMaintenanceKpis(plant ? { plant_id: plant.id, start: range.start, stop: range.stop } : null);
  const d = kpis.data;
  const listLink = (extra: string) => `/maintenance/work-orders?view=list&${extra}`;

  return (
    <div className="mx-auto max-w-7xl space-y-5 animate-rise-in">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-title text-fg">Gestão da manutenção</h2>
          <p className="text-caption text-fg-subtle">PMP, cumprimento da programação e backlog do período (fuso da planta). Cada número abre as OS que o formaram.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
        {plant && <ExportMenu report="monthly_maintenance" scopeType="plant" scopeId={plant.id} params={{ month }} />}
        <label className="flex items-center gap-2 text-caption text-fg-muted">
          Mês
          <input type="month" value={month} max={today.slice(0, 7)} onChange={(e) => set({ month: e.target.value || null })} className={clsx(inputClass, "w-auto")} />
        </label>
        </div>
      </div>

      {kpis.isPending ? (
        <LoadingRegion label="Calculando indicadores…"><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-36 rounded-xl" />)}</div></LoadingRegion>
      ) : kpis.isError || !d ? (
        <ErrorState title="Não foi possível calcular os indicadores" error={kpis.error} onRetry={() => void kpis.refetch()} />
      ) : (
        <>
          <section aria-label="Indicadores" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <KpiTile kpi="pmp" testId="kpi-pmp" label="PMP — manutenção planejada" value={d.pmp == null ? "—" : formatPct(d.pmp)}
              tone={d.pmp == null ? "neutral" : d.pmp >= 0.85 ? "ok" : d.pmp >= 0.6 ? "warn" : "danger"}
              hint={d.pmp == null ? `sem OS concluídas no período${d.pmp_reason ? ` (${d.pmp_reason})` : ""}` : "Horas planejadas ÷ horas totais das OS concluídas · meta ≥ 85 %"}
              to={listLink(`completed_from=${encodeURIComponent(range.start)}&completed_to=${encodeURIComponent(range.stop)}`)} />
            <KpiTile kpi="compliance" testId="kpi-compliance" label="Cumprimento da programação" value={d.schedule_compliance == null ? "—" : formatPct(d.schedule_compliance)}
              tone={d.schedule_compliance == null ? "neutral" : d.schedule_compliance >= 0.9 ? "ok" : d.schedule_compliance >= 0.7 ? "warn" : "danger"}
              hint={d.schedule_compliance == null ? "sem OS programadas no período" : "OS concluídas no prazo ÷ OS programadas (semana congelada)"}
              to={listLink(`from=${range.from}&to=${range.to}`)} />
            <div className="surface rounded-xl border border-line bg-surface p-4 shadow-card">
              <p className="flex items-center gap-1.5 text-caption font-medium text-fg-muted">Backlog <KpiInfo kpi="backlog" /></p>
              <p data-testid="kpi-backlog" className="mt-1.5 text-display tabular text-fg">{formatDec1(d.backlog_hours)}<span className="ml-1 text-body text-fg-subtle">h</span></p>
              <p className="mt-1 text-caption text-fg-subtle">
                {d.backlog_weeks == null ? "— capacidade da equipe não cadastrada" : `${formatDec2(d.backlog_weeks)} semana(s) de trabalho · capacidade ${formatDec1(d.crew_capacity_hours_per_week)} h/semana`}
              </p>
              {d.backlog_weeks != null && <BacklogBand weeks={d.backlog_weeks} />}
              <p className="mt-1 text-caption text-fg-subtle">Referência saudável: 2–4 semanas.</p>
              <Link to="/maintenance/schedule" className="mt-2 inline-block text-caption font-medium text-ok hover:underline">Programação da semana ›</Link>
            </div>
            <KpiTile testId="kpi-overdue" label="OS vencidas" value={String(d.overdue_count)} tone={d.overdue_count > 0 ? "danger" : "ok"}
              hint="Programadas para antes de hoje e não concluídas" to={listLink("quick=overdue")} />
          </section>

          <Card>
            <CardHeader title="Fora do cumprimento da programação" description="OS excluídas do denominador e o motivo (encaixes e substituídas)." />
            {d.compliance_excluded.length === 0 ? (
              <p className="text-caption text-fg-subtle">Nenhuma OS excluída no período.</p>
            ) : (
              <ul className="divide-y divide-line" aria-label="OS excluídas do cumprimento">
                {d.compliance_excluded.map((x) => (
                  <li key={x.wo_id} className="flex flex-wrap items-center justify-between gap-2 py-2" data-testid="excluded-row">
                    <Link to={`/maintenance/work-orders?wo=${x.wo_id}`} className="font-medium text-ok hover:underline">{x.number ?? x.wo_number ?? "OS"}</Link>
                    <span className="text-caption text-fg-muted">{EXCLUDED_REASON_LABEL[x.reason] ?? x.reason}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <p className="text-caption text-fg-subtle">
            Definições (ISO 22400 / EN 15341) em docs/kpis.md §3. Mês de {month.split("-").reverse().join("/")} · {plant?.name ?? ""}.{" "}
            <Link to="/maintenance/reliability" className="text-ok underline underline-offset-2">Confiabilidade (MTBF/MTTR) ›</Link>
          </p>
        </>
      )}
    </div>
  );
}
