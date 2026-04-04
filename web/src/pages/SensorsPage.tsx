import { useState, useEffect, useRef } from "react";
import { useAuthStore } from "@/store/auth";
import { useQueries } from "@tanstack/react-query";
import { machinesApi, tagsApi } from "@/services/api";

interface LiveReading {
  tag_id: string;
  value: number;
  unit?: string;
  timestamp: string;
}

interface SparkPoint { v: number }

const MAX_SPARK = 30;

export default function SensorsPage() {
  const { user } = useAuthStore();
  const [readings, setReadings] = useState<Record<string, LiveReading>>({});
  const [sparklines, setSparklines] = useState<Record<string, SparkPoint[]>>({});
  const [wsStatus, setWsStatus] = useState<"connecting" | "live" | "disconnected">("connecting");
  const wsRef = useRef<WebSocket | null>(null);

  // Load machines and their tags
  const machinesQuery = useQueries({
    queries: [],
  });

  const [machines, setMachines] = useState<any[]>([]);
  const [tagsByMachine, setTagsByMachine] = useState<Record<string, any[]>>({});

  useEffect(() => {
    machinesApi.list().then(r => {
      setMachines(r.data);
      r.data.forEach((m: any) => {
        tagsApi.list(m.id).then(tr => {
          setTagsByMachine(prev => ({ ...prev, [m.id]: tr.data }));
        }).catch(() => {});
      });
    }).catch(() => {});
  }, []);

  // WebSocket for live data
  useEffect(() => {
    if (!user?.tenant_id) return;
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${window.location.host}/ws/sensors/${user.tenant_id}`;

    function connect() {
      setWsStatus("connecting");
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => setWsStatus("live");

      ws.onmessage = (e) => {
        try {
          const data: LiveReading = JSON.parse(e.data);
          setReadings(prev => ({ ...prev, [data.tag_id]: data }));
          setSparklines(prev => {
            const cur = prev[data.tag_id] || [];
            const next = [...cur, { v: data.value }].slice(-MAX_SPARK);
            return { ...prev, [data.tag_id]: next };
          });
        } catch {}
      };

      ws.onclose = () => {
        setWsStatus("disconnected");
        setTimeout(connect, 3000);
      };
    }

    connect();
    return () => wsRef.current?.close();
  }, [user?.tenant_id]);

  const statusColors = {
    live: "bg-green-400",
    connecting: "bg-yellow-400 animate-pulse",
    disconnected: "bg-gray-600",
  };
  const statusLabels = { live: "Ao vivo", connecting: "Conectando…", disconnected: "Desconectado" };

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
        <div className="text-center py-20 text-gray-600">
          <p className="text-sm">Nenhuma máquina encontrada.</p>
          <p className="text-xs mt-1">Use o App Edge para cadastrar máquinas e sensores.</p>
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
                  <span className={`text-xs px-2 py-0.5 rounded-full border ${
                    machine.status === "running" ? "border-green-700 text-green-400 bg-green-900/20" :
                    machine.status === "maintenance" ? "border-yellow-700 text-yellow-400 bg-yellow-900/20" :
                    machine.status === "error" ? "border-red-700 text-red-400 bg-red-900/20" :
                    "border-gray-700 text-gray-500"
                  }`}>
                    {{running:"Operando",stopped:"Parado",maintenance:"Manutenção",error:"Falha",unknown:"—"}[machine.status as string] ?? machine.status}
                  </span>
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
                        const vals = spark.map(p => p.v);
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
                                <p className="text-xs text-gray-700">
                                  {new Date(reading.timestamp).toLocaleTimeString("pt-BR")}
                                </p>
                              </>
                            ) : (
                              <p className="text-xs text-gray-600">sem dados</p>
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
