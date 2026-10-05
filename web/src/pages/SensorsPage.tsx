import { useMemo } from "react";
import MachineStatusBadge from "@/components/MachineStatusBadge";
import EmptyState from "@/components/ui/EmptyState";
import ErrorState from "@/components/ui/ErrorState";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { useMachines, useTags } from "@/hooks/useQueries";
import { useRealtimeStore } from "@/store/realtime";
import type { Tag } from "@/services/types";

export default function SensorsPage() {
  const readings   = useRealtimeStore((s) => s.readings);
  const sparklines = useRealtimeStore((s) => s.history);
  const wsStatus   = useRealtimeStore((s) => s.status);

  const machinesQ = useMachines();
  const tagsQ     = useTags();

  const tagsByMachine = useMemo(() => {
    const map: Record<string, Tag[]> = {};
    for (const t of tagsQ.data ?? []) {
      if (t.machine_id) (map[t.machine_id] ??= []).push(t);
    }
    return map;
  }, [tagsQ.data]);

  const statusColors = {
    connected: "bg-green-400",
    connecting: "bg-yellow-400 animate-pulse",
    disconnected: "bg-gray-600",
  };
  const statusLabels = { connected: "Ao vivo", connecting: "Conectando…", disconnected: "Desconectado" };

  if (machinesQ.isPending || tagsQ.isPending) {
    return (
      <LoadingRegion label="Carregando sensores…">
        <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
          {[0, 1].map((i) => <Skeleton key={i} className="h-48 rounded-xl" />)}
        </div>
      </LoadingRegion>
    );
  }
  if (machinesQ.isError || tagsQ.isError) {
    return (
      <ErrorState
        title="Não foi possível carregar máquinas e sensores"
        error={machinesQ.error ?? tagsQ.error}
        onRetry={() => { void machinesQ.refetch(); void tagsQ.refetch(); }}
      />
    );
  }
  const machines = machinesQ.data;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-100">Sensores ao Vivo</h1>
          <p className="text-sm text-gray-500 mt-0.5">Leituras em tempo real via WebSocket</p>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <span className={`size-2 rounded-full ${statusColors[wsStatus]}`} />
          <span className="text-gray-400">{statusLabels[wsStatus]}</span>
        </div>
      </div>

      {/* Machines with sensors */}
      {machines.length === 0 ? (
        <div className="rounded-xl border border-gray-800 bg-gray-900">
          <EmptyState title="Nenhuma máquina encontrada" description="Use o App Edge para cadastrar máquinas e sensores." />
        </div>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
          {machines.map(machine => {
            const tags = tagsByMachine[machine.id] || [];
            return (
              <div key={machine.id} className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
                {/* Machine header */}
                <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-800 bg-gray-800/50">
                  <div className="size-8 rounded-lg bg-gray-700 flex items-center justify-center">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} className="size-4 text-gray-300">
                      <rect x="4" y="4" width="16" height="16" rx="2"/>
                      <path d="M9 9h6v6H9z"/>
                    </svg>
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold text-gray-100 truncate">{machine.name}</p>
                    <p className="text-xs text-gray-500">{machine.model || "—"}</p>
                  </div>
                  <MachineStatusBadge status={machine.status} size="sm" />
                </div>

                {/* Tags */}
                {tags.length === 0 ? (
                  <p className="text-xs text-gray-600 text-center py-6">Nenhum sensor cadastrado</p>
                ) : (
                  <div className="divide-y divide-gray-800">
                    {tags.map(tag => {
                      const reading = readings[tag.id];
                      const spark = sparklines[tag.id] || [];
                      const hasData = reading !== undefined;

                      // Simple inline sparkline using SVG
                      const sparkSvg = () => {
                        if (spark.length < 2) return null;
                        const vals = spark;
                        const min = Math.min(...vals);
                        const max = Math.max(...vals);
                        const range = max - min || 1;
                        const w = 60, h = 24;
                        const pts = vals.map((v, i) =>
                          `${(i / (vals.length - 1)) * w},${h - ((v - min) / range) * (h - 4) - 2}`
                        ).join(" ");
                        return (
                          <svg width={w} height={h} className="text-brand-400">
                            <polyline points={pts} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                          </svg>
                        );
                      };

                      return (
                        <div key={tag.id} className="flex items-center gap-3 px-4 py-3">
                          <div className="flex items-center gap-2 flex-1 min-w-0">
                            <span className={`size-1.5 rounded-full shrink-0 ${hasData ? "bg-brand-400" : "bg-gray-700"}`}/>
                            <div className="min-w-0">
                              <p className="text-sm text-gray-300 truncate">{tag.name}</p>
                              <p className="text-xs text-gray-600">{tag.protocol.toUpperCase()} · {tag.address}</p>
                            </div>
                          </div>
                          <div className="shrink-0">{sparkSvg()}</div>
                          <div className="text-right shrink-0 w-24">
                            {hasData ? (
                              <>
                                <p className="text-sm font-mono font-semibold text-gray-100">
                                  {reading.value.toLocaleString("pt-BR", { maximumFractionDigits: 2 })}
                                  <span className="text-xs text-gray-500 ml-1">{reading.unit || tag.unit}</span>
                                </p>
                                <p className="text-xs text-gray-500">
                                  {new Date(reading.timestamp).toLocaleTimeString("pt-BR")}
                                </p>
                              </>
                            ) : (
                              <p className="text-xs text-gray-500">sem dados</p>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
