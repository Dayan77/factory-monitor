import { useMemo } from "react";
import { format } from "date-fns";
import { ptBR } from "date-fns/locale";
import clsx from "clsx";

import StatCard from "@/components/StatCard";
import MachineStatusBadge, { STATUS_ORDER, statusLabel } from "@/components/MachineStatusBadge";
import SeverityBadge from "@/components/SeverityBadge";
import EmptyState from "@/components/ui/EmptyState";
import ErrorState from "@/components/ui/ErrorState";
import QueryView from "@/components/ui/QueryView";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import LiveReadings from "@/components/LiveReadings";
import { useLines, useMachines, useAlertEvents } from "@/hooks/useQueries";
import { useNow } from "@/hooks/useNow";
import { formatSeenAgo } from "@/utils/time";
import type { Machine, MachineStatus } from "@/services/types";

const STATUS_BAR: Record<MachineStatus, string> = {
  running: "bg-brand-500",
  stopped: "bg-gray-400",
  maintenance: "bg-yellow-500",
  error: "bg-red-500",
  unknown: "bg-gray-600",
};

const SEVERITY_RANK = { critical: 0, warning: 1, info: 2 } as const;

export default function DashboardPage() {
  const linesQ    = useLines();
  const machinesQ = useMachines();
  const eventsQ   = useAlertEvents();

  const machines = machinesQ.data;
  const lines    = linesQ.data;
  const events   = eventsQ.data;

  const counts = useMemo(() => {
    const c: Record<MachineStatus, number> = { running: 0, stopped: 0, maintenance: 0, error: 0, unknown: 0 };
    for (const m of machines ?? []) c[m.status] += 1;
    return c;
  }, [machines]);

  const activeAlerts = useMemo(
    () =>
      (events ?? [])
        .filter((a) => !a.resolved_at)
        .sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || b.triggered_at.localeCompare(a.triggered_at)),
    [events],
  );
  const criticalCount = activeAlerts.filter((a) => a.severity === "critical").length;

  const lineNames = useMemo(() => new Map((lines ?? []).map((l) => [l.id, l.name])), [lines]);

  return (
    <div className="space-y-6">
      {/* KPIs — real counts only */}
      {machinesQ.isError ? (
        <ErrorState
          title="Não foi possível carregar as máquinas"
          error={machinesQ.error}
          onRetry={() => void machinesQ.refetch()}
        />
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
          {machines === undefined ? (
            <KpiSkeletons />
          ) : (
            <>
              <StatCard
                label="Máquinas operando"
                value={`${counts.running} / ${machines.length}`}
                highlight={machines.length > 0 && counts.running === machines.length}
                sub={machines.length === 0 ? "nenhuma cadastrada" : `${counts.stopped} paradas`}
              />
              <StatCard
                label="Sem comunicação"
                value={counts.unknown}
                tone={counts.unknown > 0 ? "warning" : undefined}
                sub="status desconhecido"
              />
              <StatCard
                label="Alertas ativos"
                value={events === undefined ? "—" : activeAlerts.length}
                tone={criticalCount > 0 ? "danger" : undefined}
                sub={eventsQ.isError ? "erro ao carregar" : `${criticalCount} críticos`}
              />
              <StatCard
                label="Em falha"
                value={counts.error}
                tone={counts.error > 0 ? "danger" : undefined}
                sub={`${counts.maintenance} em manutenção`}
              />
            </>
          )}
        </div>
      )}

      {/* Status distribution + OEE placeholder */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <section className="rounded-xl border border-gray-800 bg-gray-900 p-5 xl:col-span-2" aria-labelledby="dash-status">
          <h2 id="dash-status" className="mb-4 text-sm font-semibold text-gray-200">Status das máquinas</h2>
          {machinesQ.isError ? (
            <p className="text-xs text-gray-500">Indisponível.</p>
          ) : machines === undefined ? (
            <LoadingRegion><Skeleton className="h-3 w-full" /><Skeleton className="mt-4 h-4 w-2/3" /></LoadingRegion>
          ) : machines.length === 0 ? (
            <EmptyState title="Nenhuma máquina cadastrada" description="Cadastre máquinas pelo App Edge para acompanhar o status." />
          ) : (
            <StatusDistribution counts={counts} total={machines.length} />
          )}
        </section>

        <section className="rounded-xl border border-dashed border-gray-700 bg-gray-900/60 p-5" aria-labelledby="dash-oee">
          <h2 id="dash-oee" className="text-sm font-semibold text-gray-200">OEE</h2>
          <EmptyState
            className="py-6"
            icon={
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} className="size-8" aria-hidden="true">
                <path d="M3 3v18h18" /><path d="m7 15 4-4 3 3 5-6" />
              </svg>
            }
            title="OEE disponível na Fase 2"
            description="O cálculo de OEE (disponibilidade × desempenho × qualidade, ISO 22400) depende dos contadores de produção e estados de máquina previstos na Fase 1–2. Nenhum valor estimado é exibido até lá."
          />
        </section>
      </div>

      <LiveReadings />

      {/* Alerts + machine table */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <section className="rounded-xl border border-gray-800 bg-gray-900 p-5" aria-labelledby="dash-alerts">
          <h2 id="dash-alerts" className="mb-4 text-sm font-semibold text-gray-200">Alertas ativos</h2>
          <QueryView
            query={eventsQ}
            errorTitle="Não foi possível carregar os alertas"
            loading={<LoadingRegion><div className="space-y-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-16 w-full" />)}</div></LoadingRegion>}
          >
            {() =>
              activeAlerts.length === 0 ? (
                <EmptyState className="py-6" title="Nenhum alerta ativo" description="Tudo em ordem no momento." />
              ) : (
                <ul className="space-y-3">
                  {activeAlerts.slice(0, 5).map((alert) => (
                    <li key={alert.id} className="space-y-1 rounded-lg border border-gray-800 bg-gray-950 p-3">
                      <div className="flex items-center justify-between gap-2">
                        <p className="truncate text-xs font-semibold text-gray-200">{alert.rule_name}</p>
                        <SeverityBadge severity={alert.severity} />
                      </div>
                      {alert.machine_name && <p className="truncate text-xs text-gray-500">{alert.machine_name}</p>}
                      {alert.message && <p className="text-xs text-gray-400">{alert.message}</p>}
                      <p className="text-[11px] text-gray-500">
                        {format(new Date(alert.triggered_at), "HH:mm · dd/MM", { locale: ptBR })}
                      </p>
                    </li>
                  ))}
                </ul>
              )
            }
          </QueryView>
        </section>

        <section className="rounded-xl border border-gray-800 bg-gray-900 xl:col-span-2" aria-labelledby="dash-machines">
          <div className="flex items-center justify-between border-b border-gray-800 px-5 py-4">
            <h2 id="dash-machines" className="text-sm font-semibold text-gray-200">Máquinas</h2>
            {machines && <span className="text-xs text-gray-500">{machines.length} total</span>}
          </div>
          <QueryView
            query={machinesQ}
            errorTitle="Não foi possível carregar as máquinas"
            loading={<LoadingRegion><div className="space-y-2 p-5">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-8 w-full" />)}</div></LoadingRegion>}
            isEmpty={(d) => d.length === 0}
            empty={<EmptyState title="Nenhuma máquina cadastrada" />}
          >
            {(data) => <MachineTable machines={data} lineNames={lineNames} />}
          </QueryView>
        </section>
      </div>
    </div>
  );
}

function KpiSkeletons() {
  return (
    <>
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="rounded-xl border border-gray-800 bg-gray-900 p-5" role="status" aria-busy="true">
          <span className="sr-only">Carregando…</span>
          <Skeleton className="h-4 w-24" />
          <Skeleton className="mt-3 h-8 w-16" />
          <Skeleton className="mt-2 h-3 w-20" />
        </div>
      ))}
    </>
  );
}

function StatusDistribution({ counts, total }: { counts: Record<MachineStatus, number>; total: number }) {
  return (
    <div>
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-gray-800" role="img"
        aria-label={STATUS_ORDER.map((s) => `${statusLabel(s)}: ${counts[s]}`).join(", ")}>
        {STATUS_ORDER.map((s) =>
          counts[s] > 0 ? (
            <div key={s} className={clsx("h-full transition-all duration-500", STATUS_BAR[s])} style={{ width: `${(counts[s] / total) * 100}%` }} />
          ) : null,
        )}
      </div>
      <ul className="mt-4 flex flex-wrap gap-x-6 gap-y-3">
        {STATUS_ORDER.map((s) => (
          <li key={s} className="flex items-center gap-2 whitespace-nowrap">
            <span className={clsx("size-2.5 shrink-0 rounded-full", STATUS_BAR[s])} aria-hidden="true" />
            <span className="text-xs text-gray-400">{statusLabel(s)}</span>
            <span className="font-mono text-sm font-semibold tabular-nums text-gray-100">{counts[s]}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function MachineTable({ machines, lineNames }: { machines: Machine[]; lineNames: Map<string, string> }) {
  const now = useNow(1000);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-800">
            {["Máquina", "Linha", "Status", "Último sinal"].map((h) => (
              <th key={h} scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {machines.map((m) => (
            <tr key={m.id} className="border-b border-gray-800/60 transition-colors last:border-b-0 hover:bg-gray-800/40">
              <td className="px-4 py-3 font-medium text-gray-200">{m.name}</td>
              <td className="px-4 py-3 text-gray-400">{lineNames.get(m.line_id) ?? "—"}</td>
              <td className="px-4 py-3"><MachineStatusBadge status={m.status} size="sm" /></td>
              <td className="whitespace-nowrap px-4 py-3 text-xs text-gray-400">{formatSeenAgo(m.last_seen_at, now)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
