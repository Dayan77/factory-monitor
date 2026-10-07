import { useMemo, useState } from "react";
import { formatDateTime } from "@/utils/format";
import clsx from "clsx";
import MachineStatusBadge from "@/components/MachineStatusBadge";
import EmptyState from "@/components/ui/EmptyState";
import QueryView from "@/components/ui/QueryView";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { useMachines, useLines } from "@/hooks/useQueries";
import { useNow } from "@/hooks/useNow";
import { formatSeenAgo, secondsSince } from "@/utils/time";
import type { Machine, MachineStatus } from "@/services/types";

const STATUS_FILTERS: { label: string; value: MachineStatus | "all" }[] = [
  { label: "Todas", value: "all" },
  { label: "Operando", value: "running" },
  { label: "Parada", value: "stopped" },
  { label: "Manutenção", value: "maintenance" },
  { label: "Falha", value: "error" },
  { label: "Sem comunicação", value: "unknown" },
];

const STATUS_HINT: Record<MachineStatus, string> = {
  running: "Recebendo dados.",
  stopped: "Máquina parada.",
  maintenance: "Em manutenção (declarado pelo operador).",
  error: "Falha declarada — verificar equipamento.",
  unknown: "Sem leituras recentes do gateway.",
};

export default function MachinesPage() {
  const [lineFilter,   setLineFilter]   = useState("all");
  const [statusFilter, setStatusFilter] = useState<MachineStatus | "all">("all");

  const linesQ    = useLines();
  const machinesQ = useMachines();
  const lines = linesQ.data ?? [];
  const lineNames = useMemo(() => new Map((linesQ.data ?? []).map((l) => [l.id, l.name])), [linesQ.data]);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <label className="sr-only" htmlFor="machines-line-filter">Filtrar por linha</label>
        <select
          id="machines-line-filter"
          value={lineFilter}
          onChange={(e) => setLineFilter(e.target.value)}
          disabled={linesQ.isPending || linesQ.isError}
          className="min-h-11 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200 focus:outline-none focus:ring-2 focus:ring-brand-500 disabled:opacity-60"
        >
          <option value="all">{linesQ.isError ? "Linhas indisponíveis" : "Todas as linhas"}</option>
          {lines.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
        </select>

        <div className="flex max-w-full overflow-x-auto rounded-lg border border-gray-700" role="group" aria-label="Filtrar por status">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.value}
              type="button"
              aria-pressed={statusFilter === f.value}
              onClick={() => setStatusFilter(f.value)}
              className={clsx(
                "min-h-11 whitespace-nowrap px-3 py-2 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500",
                statusFilter === f.value
                  ? "bg-accent text-accent-fg"
                  : "bg-gray-800 text-gray-400 hover:bg-gray-700"
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      <QueryView
        query={machinesQ}
        errorTitle="Não foi possível carregar as máquinas"
        loading={
          <LoadingRegion label="Carregando máquinas…">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-36 rounded-xl" />)}
            </div>
          </LoadingRegion>
        }
        isEmpty={(d) => d.length === 0}
        empty={
          <div className="rounded-xl border border-gray-800 bg-gray-900">
            <EmptyState title="Nenhuma máquina cadastrada" description="Use o App Edge para cadastrar máquinas e sensores." />
          </div>
        }
      >
        {(machines) => {
          const filtered = machines.filter((m) =>
            (lineFilter === "all" || m.line_id === lineFilter) && (statusFilter === "all" || m.status === statusFilter),
          );
          return (
            <>
              <p className="text-xs text-fg-muted" aria-live="polite">
                {filtered.length} de {machines.length} máquinas
              </p>
              {filtered.length === 0 ? (
                <div className="rounded-xl border border-gray-800 bg-gray-900">
                  <EmptyState title="Nenhuma máquina corresponde aos filtros" />
                </div>
              ) : (
                <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
                  {filtered.map((m) => (
                    <MachineCard key={m.id} machine={m} lineName={lineNames.get(m.line_id)} />
                  ))}
                </ul>
              )}
            </>
          );
        }}
      </QueryView>
    </div>
  );
}

function MachineCard({ machine: m, lineName }: { machine: Machine; lineName?: string }) {
  const now = useNow(1000);
  const age = secondsSince(m.last_seen_at, now);
  return (
    <li
      data-testid="machine-card"
      className={clsx(
        "min-w-0 space-y-3 rounded-xl border p-5",
        m.status === "error"         ? "border-red-500/30 bg-red-500/5"
        : m.status === "maintenance" ? "border-yellow-500/20 bg-yellow-500/5"
        : "border-gray-800 bg-gray-900"
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate font-semibold text-gray-100">{m.name}</p>
          <p className="truncate text-xs text-fg-muted">{[m.model, lineName].filter(Boolean).join(" · ") || "—"}</p>
        </div>
        <span className="shrink-0"><MachineStatusBadge status={m.status} /></span>
      </div>

      <p className="text-xs text-gray-400">{STATUS_HINT[m.status] ?? STATUS_HINT.unknown}</p>

      <p
        className={clsx(
          "flex items-center gap-1.5 text-xs tabular-nums",
          age == null ? "text-fg-muted" : age > 60 ? "text-yellow-400" : "text-gray-400"
        )}
        title={m.last_seen_at ? formatDateTime(m.last_seen_at) : undefined}
      >
        <svg viewBox="0 0 20 20" fill="currentColor" className="size-3.5" aria-hidden="true">
          <path fillRule="evenodd" d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16zm.75-13a.75.75 0 0 0-1.5 0v5c0 .414.336.75.75.75h4a.75.75 0 0 0 0-1.5h-3.25V5z" clipRule="evenodd" />
        </svg>
        {formatSeenAgo(m.last_seen_at, now)}
      </p>
    </li>
  );
}
