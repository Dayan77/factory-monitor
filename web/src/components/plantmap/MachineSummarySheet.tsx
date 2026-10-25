import { Link } from "react-router-dom";
import Sheet from "@/components/ui/Sheet";
import { Skeleton } from "@/components/ui/Skeleton";
import { useDowntime, useReasonCodes } from "@/hooks/useEfficiency";
import type { MachineVisual } from "@/services/sprint6Types";
import { formatDateTime, formatDec1, formatPct } from "@/utils/format";
import { formatDuration, reasonLabel } from "@/utils/efficiency";
import { drillLink, drillPaths, type DrillContext } from "@/utils/drilldown";
import { markerText } from "@/utils/visualState";
import { StateChip } from "./StateIcon";
import { SUSPECT_TITLE, badgeText, oeeText } from "@/utils/mapKpis";

interface Props {
  machine: MachineVisual | null;
  now: number;
  ctx: DrillContext;
  onClose: () => void;
}

/** Click on a marker (§1.4): state, open stop, open failure, predictive alert, shift OEE, last 3 stops → machine. */
export default function MachineSummarySheet({ machine, now, ctx, onClose }: Props) {
  const stops = useDowntime(machine ? { machine_id: machine.machine_id, limit: 3 } : null, 30_000);
  const reasons = useReasonCodes();
  const pred = machine?.badges.find((b) => b.kind === "predictive");
  return (
    <Sheet open={!!machine} onClose={onClose} title={machine?.name ?? ""}
      description={machine ? `${machine.line_name ?? ""}` : undefined}
      footer={machine && (
        <Link to={drillLink(drillPaths.machine(machine.machine_id), ctx)} data-testid="open-machine"
          className="flex min-h-11 w-full items-center justify-center rounded-lg bg-accent px-4 text-body font-semibold text-accent-fg hover:bg-accent-hover">
          Abrir máquina
        </Link>
      )}>
      {machine && (
        <div className="space-y-5">
          <div className="space-y-2">
            <StateChip state={machine.state} className="text-body" />
            <p className="text-body text-fg">{markerText(machine, now)}</p>
          </div>
          <dl className="grid grid-cols-2 gap-3">
            <div className="rounded-lg border border-line p-3">
              <dt className="text-caption text-fg-subtle">OEE do turno</dt>
              <dd className="text-title tabular text-fg">{machine.kpis?.oee == null ? "—" : formatPct(machine.kpis.oee)}</dd>
              {oeeText(machine.kpis).suspect && <dd className="text-caption text-[#b45309]" data-testid="summary-suspect">⚠ {SUSPECT_TITLE}</dd>}
              {machine.kpis?.oee == null && machine.kpis?.reason && <dd className="text-caption text-fg-subtle">{machine.kpis.reason.replace(/_/g, " ")}</dd>}
            </div>
            <div className="rounded-lg border border-line p-3">
              <dt className="text-caption text-fg-subtle">Cadência ao vivo (15 min)</dt>
              <dd className="text-title tabular text-fg">{machine.kpis?.cadence_per_hour == null ? "—" : formatDec1(machine.kpis.cadence_per_hour)} <span className="text-caption text-fg-subtle">pç/h</span></dd>
              {machine.kpis?.cadence_shift_per_hour != null && <dd className="text-caption text-fg-subtle">média do turno {formatDec1(machine.kpis.cadence_shift_per_hour)} pç/h</dd>}
            </div>
          </dl>
          {machine.badges.map(badgeText).filter(Boolean).length > 0 && (
            <ul className="space-y-1 text-caption text-fg-muted" data-testid="summary-badges">
              {machine.badges.map(badgeText).filter(Boolean).map((t) => <li key={t}>ⓘ {t}</li>)}
            </ul>
          )}
          <ul className="space-y-2 text-body">
            {machine.detail.downtime_id && (
              <li className="rounded-lg border border-[#b45309]/40 bg-[#f59e0b]/10 p-3">
                <p className="font-medium text-fg">Parada aberta</p>
                <p className="text-caption text-fg-muted">{reasonLabel(machine.detail.reason_code, reasons.data)}</p>
                <Link to={`/stops?line=${machine.line_id}`} className="text-caption font-medium text-ok hover:underline">Justificar</Link>
              </li>
            )}
            {machine.state === "failure" && (
              <li className="rounded-lg border border-[#b91c1c]/40 bg-[#b91c1c]/10 p-3">
                <p className="font-medium text-fg">Falha aberta{machine.detail.failure_code ? ` — ${machine.detail.failure_code}` : ""}</p>
                {machine.detail.fault_code != null && <p className="text-caption text-fg-muted">Código do CLP {machine.detail.fault_code}</p>}
                <Link to="/maintenance/failures" className="text-caption font-medium text-ok hover:underline">Ver falhas</Link>
              </li>
            )}
            {pred && (
              <li className="rounded-lg border border-line p-3">
                <p className="font-medium text-fg">Alerta preditivo ({pred.severity})</p>
                {pred.alert_id && <Link to={`/maintenance/predictive?alert=${pred.alert_id}`} className="text-caption font-medium text-ok hover:underline">Abrir alerta</Link>}
              </li>
            )}
          </ul>
          <section aria-labelledby="last-stops">
            <h3 id="last-stops" className="mb-2 text-caption font-semibold uppercase tracking-wide text-fg-subtle">Últimas 3 paradas</h3>
            {stops.isPending ? <Skeleton className="h-24" /> : stops.isError ? (
              <p className="text-caption text-danger">Não foi possível carregar as paradas.</p>
            ) : (stops.data ?? []).length === 0 ? (
              <p className="text-caption text-fg-subtle">Nenhuma parada registrada.</p>
            ) : (
              <ul className="divide-y divide-line rounded-lg border border-line">
                {(stops.data ?? []).slice(0, 3).map((s) => (
                  <li key={s.id} className="flex items-center justify-between gap-3 px-3 py-2">
                    <div className="min-w-0">
                      <p className="truncate text-body text-fg">{reasonLabel(s.effective_reason_code ?? s.reason_code, reasons.data)}</p>
                      <p className="text-caption text-fg-subtle">{formatDateTime(s.start)}</p>
                    </div>
                    <span className="shrink-0 tabular text-caption text-fg-muted">{s.end ? formatDuration(s.duration_s) : "em aberto"}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </Sheet>
  );
}
