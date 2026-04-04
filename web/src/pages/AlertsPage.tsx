import { useState } from "react";
import { format } from "date-fns";
import { ptBR } from "date-fns/locale";
import SeverityBadge from "@/components/SeverityBadge";
import { useAlertEvents, useResolveAlert } from "@/hooks/useQueries";
import { MOCK_ALERTS } from "@/services/mock";
import type { AlertEvent } from "@/services/types";
import clsx from "clsx";

type Filter = "all" | "active" | "resolved";

export default function AlertsPage() {
  const [filter, setFilter] = useState<Filter>("all");

  const { data: events = MOCK_ALERTS as AlertEvent[] } = useAlertEvents();
  const resolve = useResolveAlert();

  const filtered = events.filter((a) => {
    if (filter === "active")   return !a.resolved_at;
    if (filter === "resolved") return !!a.resolved_at;
    return true;
  });

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        {(["all", "active", "resolved"] as Filter[]).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={clsx(
              "rounded-lg px-4 py-2 text-sm font-medium transition-colors",
              filter === f ? "bg-brand-500 text-white" : "bg-gray-800 text-gray-400 hover:bg-gray-700"
            )}
          >
            {{ all: "Todos", active: "Ativos", resolved: "Resolvidos" }[f]}
          </button>
        ))}
        <span className="ml-auto text-xs text-gray-500">{filtered.length} alertas</span>
      </div>

      <div className="space-y-3">
        {filtered.length === 0 && (
          <div className="rounded-xl border border-gray-800 bg-gray-900 px-5 py-12 text-center">
            <p className="text-sm text-gray-500">Nenhum alerta encontrado.</p>
          </div>
        )}
        {filtered.map((alert) => (
          <div
            key={alert.id}
            className={clsx(
              "rounded-xl border p-4 flex items-start gap-4",
              alert.severity === "critical" && !alert.resolved_at ? "border-red-500/30 bg-red-500/5"
              : alert.severity === "warning" && !alert.resolved_at ? "border-yellow-500/20 bg-yellow-500/5"
              : "border-gray-800 bg-gray-900"
            )}
          >
            <span className={clsx(
              "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg",
              alert.severity === "critical" ? "bg-red-500/15 text-red-400"
              : alert.severity === "warning" ? "bg-yellow-500/15 text-yellow-400"
              : "bg-blue-500/15 text-blue-400"
            )}>
              <svg viewBox="0 0 20 20" fill="currentColor" className="size-4">
                <path fillRule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 0 1 .75.75v3.5a.75.75 0 0 1-1.5 0v-3.5A.75.75 0 0 1 10 5zm0 9a1 1 0 1 0 0-2 1 1 0 0 0 0 2z" clipRule="evenodd" />
              </svg>
            </span>

            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <p className="font-semibold text-sm text-gray-100">{alert.rule_name}</p>
                <SeverityBadge severity={alert.severity} />
                {alert.resolved_at && (
                  <span className="text-xs text-brand-500 bg-brand-500/10 rounded-full px-2 py-0.5">Resolvido</span>
                )}
              </div>
              <p className="text-xs text-gray-500 mt-0.5">{alert.machine_name}</p>
              <p className="text-sm text-gray-300 mt-1.5">{alert.message}</p>
              <div className="mt-2 flex items-center gap-4 text-xs text-gray-600">
                <span>
                  {format(new Date(alert.triggered_at), "dd/MM/yyyy 'às' HH:mm", { locale: ptBR })}
                </span>
                {!alert.resolved_at && (
                  <button
                    onClick={() => resolve.mutate(alert.id)}
                    disabled={resolve.isPending}
                    className="text-brand-500 hover:text-brand-400 font-medium transition-colors"
                  >
                    Marcar como resolvido
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
