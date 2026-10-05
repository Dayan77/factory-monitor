import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import clsx from "clsx";
import Button from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import EmptyState from "@/components/ui/EmptyState";
import ErrorState from "@/components/ui/ErrorState";
import QueryView from "@/components/ui/QueryView";
import { Field, inputClass } from "@/components/ui/Field";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/Table";
import KpiRing from "@/components/efficiency/KpiRing";
import ApqBreakdown from "@/components/efficiency/ApqBreakdown";
import DataWarnings from "@/components/efficiency/DataWarnings";
import { ParetoChart } from "@/components/efficiency/charts";
import { usePlant, useReasonCodes, useShiftReport, useShifts } from "@/hooks/useEfficiency";
import { usePrintLightTheme } from "@/hooks/usePrintLightTheme";
import type { ShiftReport } from "@/services/types";
import { formatDate, formatDateTime, formatDec1, formatInt, formatPct } from "@/utils/format";
import { formatDuration, reasonLabel, shapePareto } from "@/utils/efficiency";
import { localDate } from "@/utils/period";

function ReportBody({ r, lineName, reasons }: { r: ShiftReport; lineName: string; reasons: Parameters<typeof reasonLabel>[1] }) {
  const bars = useMemo(() => shapePareto(r.pareto), [r.pareto]);
  const o = r.oee;
  if (r.reason === "no_shift") {
    return <Card><EmptyState title="Sem turno neste dia" description="Não há turno programado para a data escolhida (feriado, fim de semana ou turno inativo)." /></Card>;
  }
  const unjustified = r.downtime.filter((d) => !d.reason_code || d.reason_code === "UNK-01").length;
  return (
    <article className="space-y-4" aria-label="Relatório de turno">
      <header className="print-avoid-break flex flex-wrap items-end justify-between gap-2 border-b border-line pb-3">
        <div>
          <p className="text-overline uppercase text-fg-subtle">Relatório de turno</p>
          <h2 className="text-title text-fg">{lineName} · {r.shift_name ?? "Dia inteiro"}</h2>
          <p className="text-caption text-fg-subtle">
            Dia produtivo {formatDate(r.productive_day)}{r.period_start && ` · ${formatDateTime(r.period_start)} → ${formatDateTime(r.period_end)}`}
          </p>
        </div>
        <p className="text-caption text-fg-subtle">Emitido em {formatDateTime(new Date().toISOString())}</p>
      </header>

      {o && (
        <>
          <DataWarnings dataCoverage={o.data_coverage} productUnknown={o.product_unknown} calendarDefault={o.calendar_default}
            idealCycleSuspect={o.ideal_cycle_time_suspect} reason={o.reason}
                    producedOutside={o.produced_outside_schedule?.total} countsProrated={o.counts_prorated} />
          <Card className="print-avoid-break grid items-center gap-6 sm:grid-cols-[auto_1fr_1fr]">
            <KpiRing value={o.oee} label="OEE" size={140} />
            <ApqBreakdown availability={o.availability} performance={o.performance} quality={o.quality} />
            <dl className="grid grid-cols-2 gap-3 text-body">
              <div><dt className="text-caption text-fg-subtle">Boas</dt><dd className="text-heading tabular text-fg">{formatInt(o.good)}</dd></div>
              <div><dt className="text-caption text-fg-subtle">Refugo</dt><dd className="text-heading tabular text-fg">{formatInt(o.scrap)}</dd></div>
              <div><dt className="text-caption text-fg-subtle">Tempo rodando</dt><dd className="tabular text-fg">{formatDuration(o.apt_s)}</dd></div>
              <div><dt className="text-caption text-fg-subtle">Paradas</dt><dd className="tabular text-fg">{formatDuration(o.adot_s)}</dd></div>
              <div><dt className="text-caption text-fg-subtle">Micro-paradas</dt><dd className="tabular text-fg">{formatInt(o.micro_stop_count)} · {formatDuration(o.micro_stop_loss_s)}</dd></div>
              <div><dt className="text-caption text-fg-subtle">Cadência</dt><dd className="tabular text-fg">{o.cadence_planned_per_hour == null ? "—" : `${formatDec1(o.cadence_planned_per_hour)} peças/h`}</dd></div>
            </dl>
          </Card>
        </>
      )}

      <Card className="print-avoid-break">
        <CardHeader title="Produção por máquina" />
        {r.production.length === 0 ? <EmptyState className="py-4" title="Sem máquinas" /> : (
          <Table caption="Produção por máquina">
            <THead><tr><TH>Máquina</TH><TH numeric>Boas</TH><TH numeric>Refugo</TH><TH numeric>D</TH><TH numeric>P</TH><TH numeric>Q</TH><TH numeric>OEE</TH></tr></THead>
            <TBody>
              {r.production.map((m) => (
                <TR key={m.machine_id}>
                  <TD className="font-medium">{m.machine_name}</TD>
                  <TD numeric>{formatInt(m.good)}</TD><TD numeric>{formatInt(m.scrap)}</TD>
                  <TD numeric>{formatPct(m.availability)}</TD><TD numeric>{formatPct(m.performance)}</TD>
                  <TD numeric>{formatPct(m.quality)}</TD><TD numeric className="font-semibold">{formatPct(m.oee)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Card>

      <Card className="print-avoid-break">
        <CardHeader title="Pareto de perdas" />
        {bars.length === 0 ? <EmptyState className="py-4" title="Nenhuma perda no turno" /> : <ParetoChart bars={bars} height={260} />}
      </Card>

      <Card>
        <CardHeader title="Paradas do turno" description={unjustified ? `${unjustified} sem justificativa` : "Todas justificadas"}
          action={unjustified ? <Link to={`/stops?line=${r.line_id}`} className="text-caption font-medium text-ok hover:underline print:hidden">Justificar</Link> : undefined} />
        {r.downtime.length === 0 ? <EmptyState className="py-4" title="Nenhuma parada" /> : (
          <Table caption="Paradas do turno">
            <THead><tr><TH>Início</TH><TH>Máquina</TH><TH>Motivo</TH><TH numeric>Duração</TH><TH>Comentário</TH></tr></THead>
            <TBody>
              {r.downtime.map((d) => (
                <TR key={d.id}>
                  <TD className="whitespace-nowrap tabular">{formatDateTime(d.start)}</TD>
                  <TD>{d.machine_name ?? "—"}</TD>
                  <TD className={clsx((!d.reason_code || d.reason_code === "UNK-01") && "text-warn")}>{reasonLabel(d.reason_code, reasons)}{d.planned && " (planejada)"}</TD>
                  <TD numeric>{d.end ? formatDuration(d.duration_s) : "em andamento"}</TD>
                  <TD className="max-w-[16rem] truncate text-fg-muted" title={d.comment ?? undefined}>{d.comment ?? ""}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Card>
    </article>
  );
}

/** Shift report (screen + print / "Salvar como PDF" via the browser's print dialog). */
export default function ShiftReportPage() {
  usePrintLightTheme();
  const { tree, plant, tz } = usePlant();
  const shifts = useShifts(plant?.id);
  const reasons = useReasonCodes();
  const [params, setParams] = useSearchParams();
  const lines = plant?.lines ?? [];
  const lineId = params.get("line") ?? lines[0]?.id ?? "";
  const day = params.get("day") ?? localDate(Date.now(), tz);
  const shiftId = params.get("shift") ?? "";
  const report = useShiftReport(lineId ? { line_id: lineId, productive_day: day, ...(shiftId ? { shift_id: shiftId } : {}) } : null);
  const lineName = lines.find((l) => l.id === lineId)?.name ?? "";

  function set(k: string, v: string) {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v); else next.delete(k);
    setParams(next, { replace: true });
  }

  if (tree.isError) return <ErrorState error={tree.error} onRetry={() => void tree.refetch()} />;

  return (
    <div className="mx-auto max-w-5xl space-y-5 animate-rise-in">
      <div className="flex flex-wrap items-end gap-3 rounded-xl border border-line bg-surface p-3 shadow-card print:hidden">
        <Field label="Linha" htmlFor="rep-line" className="min-w-[12rem] flex-1">
          <select id="rep-line" value={lineId} onChange={(e) => set("line", e.target.value)} className={inputClass}>
            {lines.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
        </Field>
        <Field label="Dia produtivo" htmlFor="rep-day">
          <input id="rep-day" type="date" value={day} max={localDate(Date.now(), tz)} onChange={(e) => set("day", e.target.value)} className={inputClass} />
        </Field>
        <Field label="Turno" htmlFor="rep-shift" className="min-w-[10rem]">
          <select id="rep-shift" value={shiftId} onChange={(e) => set("shift", e.target.value)} className={inputClass}>
            <option value="">Dia inteiro</option>
            {(shifts.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.name} ({s.start_local}–{s.end_local})</option>)}
          </select>
        </Field>
        <Button variant="primary" onClick={() => window.print()} disabled={!report.data}>Imprimir / PDF</Button>
      </div>

      {!lineId ? (
        tree.isPending ? <Skeleton className="h-64 rounded-xl" /> : <Card><EmptyState title="Nenhuma linha cadastrada" /></Card>
      ) : (
        <QueryView query={report} errorTitle="Não foi possível gerar o relatório"
          loading={<LoadingRegion label="Gerando relatório…"><Skeleton className="h-16" /><Skeleton className="mt-4 h-48 rounded-xl" /><Skeleton className="mt-4 h-48 rounded-xl" /></LoadingRegion>}>
          {(r) => <ReportBody r={r} lineName={lineName} reasons={reasons.data} />}
        </QueryView>
      )}
    </div>
  );
}
