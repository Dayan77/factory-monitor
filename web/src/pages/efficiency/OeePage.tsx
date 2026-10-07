import { useMemo } from "react";
import KpiInfo from "@/components/ui/KpiInfo";
import { Link } from "react-router-dom";
import clsx from "clsx";
import { Card, CardHeader } from "@/components/ui/Card";
import Stat from "@/components/ui/Stat";
import EmptyState from "@/components/ui/EmptyState";
import ErrorState from "@/components/ui/ErrorState";
import QueryView from "@/components/ui/QueryView";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { Tabs, TabPanel } from "@/components/ui/Tabs";
import { inputClass } from "@/components/ui/Field";
import KpiRing from "@/components/efficiency/KpiRing";
import DataWarnings from "@/components/efficiency/DataWarnings";
import PeriodPicker from "@/components/efficiency/PeriodPicker";
import StateTimeline from "@/components/efficiency/StateTimeline";
import { OeeTrendChart, ParetoChart, WaterfallChart } from "@/components/efficiency/charts";
import { useDowntime, useOee, usePareto, usePlant, useReasonCodes, useReliability, useShifts } from "@/hooks/useEfficiency";
import type { OeeGroupBy, OeeResult, OeeScope, TreePlant } from "@/services/types";
import { formatDec1, formatInt, formatPct } from "@/utils/format";
import { buildWaterfall, formatDuration, formatHours, oeeTone, shapePareto, trendLabel } from "@/utils/efficiency";
import { usePeriodParams } from "./usePeriodParams";
import ExportMenu from "@/components/reports/ExportMenu";
import { localDate } from "@/utils/period";
import { weekStart } from "@/utils/reports";

const SCOPES = [
  { value: "plant", label: "Planta" },
  { value: "line", label: "Linha" },
  { value: "machine", label: "Máquina" },
] as const;

const GROUPS = [
  { value: "hour", label: "Hora" },
  { value: "shift", label: "Turno" },
  { value: "day", label: "Dia" },
] as const;

function scopeName(plant: TreePlant | null, scope: OeeScope, id: string): string {
  if (!plant) return "";
  if (scope === "plant") return plant.name;
  for (const l of plant.lines) {
    if (scope === "line" && l.id === id) return l.name;
    const m = l.machines.find((mm) => mm.id === id);
    if (scope === "machine" && m) return `${m.name} · ${l.name}`;
  }
  return "";
}

function Headline({ o }: { o: OeeResult }) {
  return (
    <div className="grid gap-4 lg:grid-cols-[auto_1fr]">
      <Card className="flex items-center justify-center">
        <KpiRing value={o.oee} label="OEE" size={168} valueClassName="text-[2.5rem]" />
      </Card>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
        <Stat kpi="availability" label="Disponibilidade" value={formatPct(o.availability)} tone={oeeTone(o.availability)} hint={`Rodando ${formatDuration(o.apt_s)} de ${formatDuration(o.pbt_s)}`} />
        <Stat kpi="performance" label="Desempenho" value={formatPct(o.performance)} tone={oeeTone(o.performance)} hint={o.cadence_running_per_hour != null ? `${formatDec1(o.cadence_running_per_hour)} peças/h rodando` : undefined} />
        <Stat kpi="quality" label="Qualidade" value={formatPct(o.quality)} tone={oeeTone(o.quality)} hint={`${formatInt(o.good)} boas · ${formatInt(o.scrap)} refugo`} />
        <Stat kpi="teep" label="TEEP" value={formatPct(o.teep)} hint={o.loading != null ? `Carga ${formatPct(o.loading)} do calendário` : "OEE × carga"} />
        <Stat kpi="micro_stops" label="Micro-paradas" value={formatInt(o.micro_stop_count)} hint={`${formatDuration(o.micro_stop_loss_s)} perdidos`} tone={o.micro_stop_count > 0 ? "warn" : "neutral"} />
        <Stat label="Perda de velocidade" value={formatDuration(o.speed_loss_s)} />
        <Stat label="Paradas (ADOT)" value={formatDuration(o.adot_s)} tone={o.adot_s > 0 ? "warn" : "neutral"} />
        <Stat kpi="cadence" label="Cadência realizada" value={o.cadence_planned_per_hour == null ? "—" : formatDec1(o.cadence_planned_per_hour)} unit="peças/h" />
      </div>
    </div>
  );
}

function ReliabilityCards({ scope, id, start, stop }: { scope: OeeScope; id: string; start: string; stop: string }) {
  const q = useReliability(scope === "plant" ? null : { scope, id, start, stop });
  if (scope === "plant") {
    return <EmptyState className="py-6" title="Escolha uma linha ou máquina" description="MTBF e MTTR são calculados por máquina ou linha (ISO 14224)." />;
  }
  return (
    <QueryView query={q} loading={<LoadingRegion><div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-24 rounded-xl" />)}</div></LoadingRegion>}
      errorTitle="Não foi possível carregar a confiabilidade">
      {(r) => (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat kpi="mtbf" label="MTBF" value={formatHours(r.mtbf_h)} hint={`${formatDec1(r.operating_h)} h de operação`} />
            <Stat kpi="mttr" label="MTTR" value={r.mttr_s != null ? formatDuration(r.mttr_s) : formatHours(r.mttr_h)} hint="tempo médio até restabelecer" />
            <Stat label="Falhas" value={formatInt(r.failures)} hint={r.short_faults ? `+ ${r.short_faults} falhas curtas` : "críticas e degradadas"} tone={r.failures > 0 ? "warn" : "neutral"} />
            <Stat kpi="ao" label="Disponibilidade inerente" value={formatPct(r.availability_inherent)} hint="MTBF / (MTBF + MTTR)" />
          </div>
          {r.reason === "no_failures" && <p className="text-caption text-fg-subtle">Nenhuma falha registrada no período — MTBF indefinido.</p>}
        </div>
      )}
    </QueryView>
  );
}

export default function OeePage() {
  const { tree, plant, tz } = usePlant();
  const shifts = useShifts(plant?.id);
  const pp = usePeriodParams(tz, shifts.data);
  const { params, update, period, groupBy } = pp;
  const scope = (["plant", "line", "machine"].includes(params.get("scope") ?? "") ? params.get("scope") : "plant") as OeeScope;
  const lines = plant?.lines ?? [];
  const allMachines = useMemo(() => lines.flatMap((l) => l.machines.map((m) => ({ ...m, line: l.name, line_id: l.id }))), [lines]);
  const id = scope === "plant" ? plant?.id ?? "" : params.get("id") ?? (scope === "line" ? lines[0]?.id : allMachines[0]?.id) ?? "";

  const win = period.unavailable ? null : { start: period.start, stop: period.stop };
  const oee = useOee(win && id ? { scope, id, ...win, group_by: groupBy } : null);
  const pareto = usePareto(win && id ? { scope, id, ...win } : null);
  const reasons = useReasonCodes();
  const timelineMachines = scope === "machine" ? allMachines.filter((m) => m.id === id) : scope === "line" ? allMachines.filter((m) => m.line_id === id) : [];
  const downtime = useDowntime(win && id && scope !== "plant" ? { [scope === "line" ? "line_id" : "machine_id"]: id, ...win } : null);

  const bars = useMemo(() => shapePareto(pareto.data ?? []), [pareto.data]);
  const steps = useMemo(() => (oee.data ? buildWaterfall(oee.data) : []), [oee.data]);
  const name = scopeName(plant, scope, id);

  function setScope(s: OeeScope) {
    update({ scope: s, id: s === "plant" ? null : s === "line" ? lines[0]?.id ?? null : allMachines[0]?.id ?? null });
  }

  if (tree.isError) return <ErrorState error={tree.error} onRetry={() => void tree.refetch()} />;

  return (
    <div className="mx-auto max-w-7xl space-y-6 animate-rise-in">
      <div className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0">
            <h2 className="flex min-w-0 items-center gap-2 text-title text-fg"><span className="truncate">OEE {name && `· ${name}`}</span><KpiInfo kpi="oee" /></h2>
            <p className="text-caption text-fg-subtle">{period.label} · ISO 22400 (D × P × Q)</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {plant && (
              <ExportMenu report="daily_production" scopeType="plant" scopeId={plant.id} label="Exportar dia"
                params={{ productive_day: localDate(Date.parse(period.start), tz) }} />
            )}
            {plant && (
              <ExportMenu report="weekly_oee" label="Exportar semana"
                scopeType={scope === "plant" ? "plant" : "line"}
                scopeId={scope === "plant" ? plant.id : scope === "line" ? id : allMachines.find((m) => m.id === id)?.line_id}
                params={{ week_start: weekStart(localDate(Date.parse(period.start), tz)) }} />
            )}
            <Link to={`/reports/shift${scope === "line" ? `?line=${id}` : ""}`} className="inline-flex min-h-10 items-center rounded-lg border border-line-strong bg-surface px-4 text-body font-medium text-fg hover:bg-surface-2">
              Relatório de turno
            </Link>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-surface p-2 shadow-card">
          <Tabs id="oee-scope" label="Escopo" size="sm" items={SCOPES} value={scope} onChange={setScope} />
          {scope === "line" && (
            <select aria-label="Linha" value={id} onChange={(e) => update({ id: e.target.value })} className={clsx(inputClass, "min-h-9 py-1 sm:!w-auto")}>
              {lines.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
          )}
          {scope === "machine" && (
            <select aria-label="Máquina" value={id} onChange={(e) => update({ id: e.target.value })} className={clsx(inputClass, "min-h-9 py-1 sm:!w-auto")}>
              {lines.map((l) => (
                <optgroup key={l.id} label={l.name}>
                  {l.machines.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                </optgroup>
              ))}
            </select>
          )}
          <span className="hidden h-6 w-px bg-line sm:block" aria-hidden="true" />
          <PeriodPicker id="oee-period" value={pp.preset} onChange={pp.setPreset} from={pp.from} to={pp.to} onCustom={pp.setCustom} />
        </div>
      </div>

      {period.unavailable ? (
        <Card><EmptyState title={period.label} description={period.unavailable} /></Card>
      ) : !id ? (
        tree.isPending ? <Skeleton className="h-64 rounded-xl" /> : <Card><EmptyState title="Nada para analisar" description="Cadastre linhas e máquinas na planta." /></Card>
      ) : (
        <>
          <section aria-label="Indicadores" className="space-y-3">
            <QueryView query={oee} errorTitle="Não foi possível calcular o OEE"
              loading={<LoadingRegion label="Calculando OEE…"><div className="grid gap-4 lg:grid-cols-[14rem_1fr]"><Skeleton className="h-48 rounded-xl" /><Skeleton className="h-48 rounded-xl" /></div></LoadingRegion>}>
              {(o) => (
                <div className={clsx("space-y-3 transition-opacity", oee.isPlaceholderData && "opacity-60")}>
                  <DataWarnings dataCoverage={o.data_coverage} productUnknown={o.product_unknown} calendarDefault={o.calendar_default}
                    idealCycleSuspect={o.ideal_cycle_time_suspect} reason={o.reason}
                    producedOutside={o.produced_outside_schedule?.total} countsProrated={o.counts_prorated} />
                  <Headline o={o} />
                </div>
              )}
            </QueryView>
          </section>

          <div className="grid gap-4 xl:grid-cols-2">
            <Card>
              <CardHeader title="Tendência" description="D, P, Q e OEE ao longo do período."
                action={<Tabs id="oee-group" label="Agrupar por" size="sm" items={GROUPS} value={groupBy === "none" ? "hour" : groupBy} onChange={(g) => pp.setGroupBy(g as OeeGroupBy)} />} />
              <TabPanel id="oee-group" activeValue={groupBy}>
                {oee.data ? (
                  oee.data.points.length === 0 ? <EmptyState title="Sem pontos no período" /> : <OeeTrendChart points={oee.data.points} labelOf={(pt) => trendLabel(pt, groupBy, tz)} />
                ) : oee.isError ? <p className="text-caption text-fg-subtle">Indisponível.</p> : <Skeleton className="h-72" />}
              </TabPanel>
            </Card>
            <Card>
              <CardHeader title="Cascata de tempo" description="Do tempo programado (POT) ao tempo produtivo líquido." />
              {oee.data ? (
                oee.data.pot_s <= 0 ? <EmptyState title="Sem tempo programado no período" /> : <WaterfallChart steps={steps} />
              ) : oee.isError ? <p className="text-caption text-fg-subtle">Indisponível.</p> : <Skeleton className="h-72" />}
            </Card>
          </div>

          <Card id="perdas">
            <CardHeader title="Pareto de perdas" description="Paradas não planejadas, micro-paradas, perda de velocidade e refugo — em % do tempo perdido. A linha mostra o acumulado (regra 80/20)." />
            <QueryView query={pareto} errorTitle="Não foi possível carregar as perdas"
              loading={<LoadingRegion><Skeleton className="h-72" /></LoadingRegion>}
              isEmpty={() => bars.length === 0} empty={<EmptyState title="Nenhuma perda no período" description="Sem paradas não planejadas, micro-paradas ou refugo." />}>
              {() => <ParetoChart bars={bars} />}
            </QueryView>
          </Card>

          <Card>
            <CardHeader title="Confiabilidade" description="MTBF e MTTR das falhas de equipamento no período." />
            {win && <ReliabilityCards scope={scope} id={id} start={win.start} stop={win.stop} />}
          </Card>

          {scope !== "plant" && (
            <Card>
              <CardHeader title="Linha do tempo de estados"
                action={<Link to={`/timeline?${scope === "line" ? `line=${id}` : `line=${allMachines.find((m) => m.id === id)?.line_id ?? ""}`}&period=${pp.preset}${pp.from ? `&from=${pp.from}&to=${pp.to}` : ""}`} className="text-caption font-medium text-ok hover:underline">Abrir</Link>} />
              <QueryView query={downtime} errorTitle="Não foi possível carregar as paradas" loading={<Skeleton className="h-24" />}>
                {(evs) => (
                  <StateTimeline tz={tz} start={Date.parse(period.start)} stop={Date.parse(period.stop)} now={Date.now()} reasons={reasons.data}
                    rows={timelineMachines.map((m) => ({ id: m.id, name: m.name, events: evs.filter((e) => e.machine_id === m.id) }))} />
                )}
              </QueryView>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
