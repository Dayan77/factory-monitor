import { useMemo } from "react";
import clsx from "clsx";
import { Link } from "react-router-dom";
import MachineStatusBadge from "@/components/MachineStatusBadge";
import EmptyState from "@/components/ui/EmptyState";
import QueryView from "@/components/ui/QueryView";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { useLines, useMachines } from "@/hooks/useQueries";
import { useNow } from "@/hooks/useNow";
import { formatSeenAgo } from "@/utils/time";
import type { Machine } from "@/services/types";

export default function LinesPage() {
  const linesQ    = useLines();
  const machinesQ = useMachines();
  const now = useNow(1000);

  const byLine = useMemo(() => {
    const map = new Map<string, Machine[]>();
    for (const m of machinesQ.data ?? []) map.set(m.line_id, [...(map.get(m.line_id) ?? []), m]);
    return map;
  }, [machinesQ.data]);

  return (
    <QueryView
      query={linesQ}
      errorTitle="Não foi possível carregar as linhas"
      loading={
        <LoadingRegion label="Carregando linhas…">
          <div className="space-y-4">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-40 rounded-xl" />)}</div>
        </LoadingRegion>
      }
      isEmpty={(d) => d.length === 0}
      empty={
        <div className="rounded-xl border border-gray-800 bg-gray-900">
          <EmptyState title="Nenhuma linha de produção cadastrada" />
        </div>
      }
    >
      {(lines) => (
        <div className="space-y-4">
          {lines.map((line) => {
            const lineMachines = byLine.get(line.id) ?? [];
            const running = lineMachines.filter((m) => m.status === "running").length;
            return (
              <section key={line.id} className="rounded-xl border border-gray-800 bg-gray-900" aria-label={line.name}>
                <div className="flex items-center justify-between gap-4 border-b border-gray-800 px-5 py-4">
                  <div className="flex min-w-0 items-center gap-3">
                    <span aria-hidden="true" className={clsx("size-2.5 shrink-0 rounded-full", line.is_active ? "bg-brand-400" : "bg-gray-600")} />
                    <div className="min-w-0">
                      <p className="truncate font-semibold text-gray-100">
                        <Link to={`/factory/lines/${line.id}`} className="underline-offset-2 hover:underline">{line.name}</Link>
                      </p>
                      <p className="truncate text-xs text-fg-muted">
                        {[line.location, line.is_active ? null : "inativa"].filter(Boolean).join(" · ") || "—"}
                      </p>
                    </div>
                  </div>
                  <div className="shrink-0 text-right">
                    <p className="text-xs text-fg-muted">Operando</p>
                    <p className="font-mono font-medium tabular-nums text-gray-200">
                      {machinesQ.data ? `${running}/${lineMachines.length}` : "—"}
                    </p>
                  </div>
                </div>

                {machinesQ.isPending ? (
                  <LoadingRegion><div className="space-y-2 p-5"><Skeleton className="h-6 w-full" /><Skeleton className="h-6 w-full" /></div></LoadingRegion>
                ) : machinesQ.isError ? (
                  <p className="px-5 py-4 text-sm text-red-300" role="alert">Não foi possível carregar as máquinas.</p>
                ) : lineMachines.length > 0 ? (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-gray-800/60">
                          {["Máquina", "Modelo", "Status", "Último sinal"].map((h) => (
                            <th key={h} scope="col" className="px-4 py-2.5 text-left text-xs font-medium text-fg-muted">{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {lineMachines.map((m) => (
                          <tr key={m.id} className="border-b border-gray-800/40 transition-colors last:border-b-0 hover:bg-gray-800/30">
                            <td className="px-4 py-3 font-medium text-gray-200">
                              <Link to={`/factory/machines/${m.id}`} className="underline-offset-2 hover:underline">{m.name}</Link>
                            </td>
                            <td className="px-4 py-3 text-fg-muted">{m.model ?? "—"}</td>
                            <td className="px-4 py-3"><MachineStatusBadge status={m.status} size="sm" /></td>
                            <td className="whitespace-nowrap px-4 py-3 text-xs text-gray-400">{formatSeenAgo(m.last_seen_at, now)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="px-5 py-4 text-sm text-fg-muted">Nenhuma máquina cadastrada.</p>
                )}
              </section>
            );
          })}
        </div>
      )}
    </QueryView>
  );
}
