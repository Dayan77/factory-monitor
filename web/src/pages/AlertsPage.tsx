import { useState } from "react";
import { format } from "date-fns";
import { ptBR } from "date-fns/locale";
import clsx from "clsx";
import SeverityBadge from "@/components/SeverityBadge";
import EmptyState from "@/components/ui/EmptyState";
import QueryView from "@/components/ui/QueryView";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { useAlertEvents, useResolveAlert } from "@/hooks/useQueries";
import type { AlertEvent } from "@/services/types";

type Filter = "all" | "active" | "resolved";

const FILTER_LABEL: Record<Filter, string> = { all: "Todos", active: "Ativos", resolved: "Resolvidos" };
const EMPTY_LABEL: Record<Filter, string> = {
  all: "Nenhum alerta registrado.",
  active: "Nenhum alerta ativo — tudo em ordem.",
  resolved: "Nenhum alerta resolvido.",
};

export default function AlertsPage() {
  const [filter, setFilter] = useState<Filter>("all");
  const eventsQ = useAlertEvents();

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-2" role="group" aria-label="Filtrar alertas">
          {(Object.keys(FILTER_LABEL) as Filter[]).map((f) => (
            <button
              key={f}
              type="button"
              aria-pressed={filter === f}
              onClick={() => setFilter(f)}
              className={clsx(
                "min-h-11 rounded-lg px-4 py-2 text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500",
                filter === f ? "bg-brand-500 text-white" : "bg-gray-800 text-gray-400 hover:bg-gray-700"
              )}
            >
              {FILTER_LABEL[f]}
            </button>
          ))}
        </div>
      </div>

      <QueryView
        query={eventsQ}
        errorTitle="Não foi possível carregar os alertas"
        loading={
          <LoadingRegion label="Carregando alertas…">
            <div className="space-y-3">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-24 rounded-xl" />)}</div>
          </LoadingRegion>
        }
      >
        {(events) => <AlertList events={events} filter={filter} />}
      </QueryView>
    </div>
  );
}

function AlertList({ events, filter }: { events: AlertEvent[]; filter: Filter }) {
  const resolve = useResolveAlert();
  const filtered = events.filter((a) =>
    filter === "active" ? !a.resolved_at : filter === "resolved" ? !!a.resolved_at : true,
  );

  return (
    <div className="space-y-3">
      <p className="text-xs text-gray-500" aria-live="polite">{filtered.length} alertas</p>
      {resolve.isError && (
        <p role="alert" className="rounded-lg border border-red-500/30 bg-red-500/5 px-4 py-2 text-xs text-red-300">
          Não foi possível marcar o alerta como resolvido. Tente novamente.
        </p>
      )}
      {filtered.length === 0 ? (
        <div className="rounded-xl border border-gray-800 bg-gray-900">
          <EmptyState title="Nenhum alerta encontrado" description={EMPTY_LABEL[filter]} />
        </div>
      ) : (
        <ul className="space-y-3">
          {filtered.map((alert) => (
            <li
              key={alert.id}
              className={clsx(
                "flex items-start gap-4 rounded-xl border p-4",
                alert.severity === "critical" && !alert.resolved_at ? "border-red-500/30 bg-red-500/5"
                : alert.severity === "warning" && !alert.resolved_at ? "border-yellow-500/20 bg-yellow-500/5"
                : "border-gray-800 bg-gray-900"
              )}
            >
              <span
                aria-hidden="true"
                className={clsx(
                  "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg",
                  alert.severity === "critical" ? "bg-red-500/15 text-red-400"
                  : alert.severity === "warning" ? "bg-yellow-500/15 text-yellow-400"
                  : "bg-blue-500/15 text-blue-400"
                )}
              >
                <svg viewBox="0 0 20 20" fill="currentColor" className="size-4">
                  <path fillRule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 0 1 .75.75v3.5a.75.75 0 0 1-1.5 0v-3.5A.75.75 0 0 1 10 5zm0 9a1 1 0 1 0 0-2 1 1 0 0 0 0 2z" clipRule="evenodd" />
                </svg>
              </span>

              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-sm font-semibold text-gray-100">{alert.rule_name}</p>
                  <SeverityBadge severity={alert.severity} />
                  {alert.resolved_at && (
                    <span className="rounded-full bg-brand-500/10 px-2 py-0.5 text-xs text-brand-400">Resolvido</span>
                  )}
                </div>
                {alert.machine_name && <p className="mt-0.5 text-xs text-gray-500">{alert.machine_name}</p>}
                {alert.message && <p className="mt-1.5 text-sm text-gray-300">{alert.message}</p>}
                <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-500">
                  <time dateTime={alert.triggered_at}>
                    {format(new Date(alert.triggered_at), "dd/MM/yyyy 'às' HH:mm", { locale: ptBR })}
                  </time>
                  {!alert.resolved_at && (
                    <button
                      type="button"
                      onClick={() => resolve.mutate(alert.id)}
                      disabled={resolve.isPending}
                      className="min-h-9 font-medium text-brand-500 transition-colors hover:text-brand-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:opacity-50"
                    >
                      Marcar como resolvido
                    </button>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
