import { useMemo } from "react";
import {
  AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend,
} from "recharts";
import { format } from "date-fns";
import { ptBR } from "date-fns/locale";

import StatCard from "@/components/StatCard";
import MachineStatusBadge from "@/components/MachineStatusBadge";
import SeverityBadge from "@/components/SeverityBadge";
import { useLines, useMachines, useAlertEvents } from "@/hooks/useQueries";
import { useRealtimeSensors } from "@/hooks/useWebSocket";
import { useAuthStore } from "@/store/auth";
import type { Machine, Line, AlertEvent } from "@/services/types";

// ── Fallback mock data (used when backend is unreachable) ─────────
import {
  MOCK_MACHINES, MOCK_ALERTS, MOCK_LINES,
  generateOeeTrend,
} from "@/services/mock";

const OEE_TREND = generateOeeTrend();

function formatHour(iso: string) {
  return format(new Date(iso), "HH:mm", { locale: ptBR });
}

const CHART_STYLE = { fontSize: 11, fill: "#6b7280" };

export default function DashboardPage() {
  const { user } = useAuthStore();

  const { data: lines    = MOCK_LINES    as Line[] }       = useLines();
  const { data: machines = MOCK_MACHINES as Machine[] }    = useMachines();
  const { data: events   = MOCK_ALERTS   as AlertEvent[] } = useAlertEvents();

  // Live sensor readings via WebSocket
  const { readings } = useRealtimeSensors(user?.tenant_id ?? "demo-tenant");

  const totalOee = useMemo(() => {
    const running = machines.filter((m) => m.status === "running");
    return running.length
      ? +(running.reduce((s, m) => s + (m.oee ?? 0), 0) / running.length).toFixed(1)
      : 0;
  }, [machines]);

  const counts = useMemo(() => ({
    running:     machines.filter((m) => m.status === "running").length,
    stopped:     machines.filter((m) => m.status === "stopped").length,
    maintenance: machines.filter((m) => m.status === "maintenance").length,
    error:       machines.filter((m) => m.status === "error").length,
  }), [machines]);

  const activeAlerts = events.filter((a) => !a.resolved_at);

  // Build live temperature series from WebSocket readings (or first mock reading)
  const liveReadings = Array.from(readings.values());

  return (
    <div className="space-y-6">
      {/* KPIs */}
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <StatCard
          label="OEE Geral"
          value={totalOee}
          unit="%"
          highlight
          trend="up"
          trendValue="vs meta 85%"
          icon={
            <svg viewBox="0 0 20 20" fill="currentColor" className="size-5">
              <path d="M2 10a8 8 0 1 1 16 0A8 8 0 0 1 2 10zm8-3a1 1 0 0 0-1 1v2a1 1 0 0 0 .553.894l2 1a1 1 0 1 0 .894-1.789L11 9.618V8a1 1 0 0 0-1-1z" />
            </svg>
          }
        />
        <StatCard
          label="Máquinas Operando"
          value={`${counts.running} / ${machines.length}`}
          trend="neutral"
          trendValue={`${lines.filter((l) => l.is_active).length} linhas ativas`}
          icon={
            <svg viewBox="0 0 20 20" fill="currentColor" className="size-5">
              <path fillRule="evenodd" d="M14.5 10a4.5 4.5 0 0 0 4.284-5.882c-.105-.324-.51-.391-.752-.15L15.34 6.66a.454.454 0 0 1-.493.11 3.01 3.01 0 0 1-1.618-1.616.455.455 0 0 1 .11-.494l2.694-2.692c.24-.241.174-.647-.15-.752a4.5 4.5 0 0 0-5.873 4.575c.055.873-.128 1.808-.8 2.368l-7.23 6.024a2.724 2.724 0 1 0 3.837 3.837l6.024-7.23c.56-.672 1.495-.855 2.368-.8.096.007.193.01.291.01z" clipRule="evenodd" />
            </svg>
          }
        />
        <StatCard
          label="Alertas Ativos"
          value={activeAlerts.length}
          trend={activeAlerts.length > 0 ? "down" : "neutral"}
          trendValue={`${activeAlerts.filter((a) => a.severity === "critical").length} críticos`}
          icon={
            <svg viewBox="0 0 20 20" fill="currentColor" className="size-5">
              <path fillRule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 0 1 .75.75v3.5a.75.75 0 0 1-1.5 0v-3.5A.75.75 0 0 1 10 5zm0 9a1 1 0 1 0 0-2 1 1 0 0 0 0 2z" clipRule="evenodd" />
            </svg>
          }
        />
        <StatCard
          label="Linhas Ativas"
          value={lines.filter((l) => l.is_active).length}
          unit={`/ ${lines.length}`}
          trend="neutral"
          trendValue={`${machines.filter((m) => m.status === "error").length} em falha`}
          icon={
            <svg viewBox="0 0 20 20" fill="currentColor" className="size-5">
              <path fillRule="evenodd" d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16zm3.857-9.809a.75.75 0 0 0-1.214-.882l-3.483 4.79-1.88-1.88a.75.75 0 1 0-1.06 1.061l2.5 2.5a.75.75 0 0 0 1.137-.089l4-5.5z" clipRule="evenodd" />
            </svg>
          }
        />
      </div>

      {/* Charts */}
      <div className="grid gap-4 xl:grid-cols-3">
        <div className="xl:col-span-2 rounded-xl border border-gray-800 bg-gray-900 p-5">
          <p className="mb-4 text-sm font-semibold text-gray-200">OEE — Últimas 24h</p>
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={OEE_TREND} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="gradOee" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="10%" stopColor="#25a368" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#25a368" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="#1f2937" vertical={false} />
              <XAxis dataKey="timestamp" tickFormatter={formatHour} tick={CHART_STYLE} axisLine={false} tickLine={false} interval={3} />
              <YAxis tick={CHART_STYLE} axisLine={false} tickLine={false} domain={[40, 100]} />
              <Tooltip
                contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 8, fontSize: 12 }}
                labelFormatter={(v) => formatHour(v as string)}
                formatter={(v: number) => [`${v.toFixed(1)}%`, ""]}
              />
              <Legend wrapperStyle={{ fontSize: 11, color: "#9ca3af" }} />
              <Area type="monotone" dataKey="oee"          stroke="#25a368" fill="url(#gradOee)" strokeWidth={2} name="OEE" dot={false} />
              <Area type="monotone" dataKey="availability" stroke="#60a5fa" fill="none"           strokeWidth={1.5} strokeDasharray="4 2" name="Disponib." dot={false} />
              <Area type="monotone" dataKey="performance"  stroke="#a78bfa" fill="none"           strokeWidth={1.5} strokeDasharray="4 2" name="Desempenho" dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        <div className="rounded-xl border border-gray-800 bg-gray-900 p-5">
          <p className="mb-4 text-sm font-semibold text-gray-200">OEE por Linha</p>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={lines} layout="vertical" margin={{ top: 0, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="#1f2937" horizontal={false} />
              <XAxis type="number" domain={[0, 100]} tick={CHART_STYLE} axisLine={false} tickLine={false} />
              <YAxis type="category" dataKey="name" width={90} tick={{ ...CHART_STYLE, fontSize: 10 }} axisLine={false} tickLine={false} tickFormatter={(v: string) => v.split("—")[0].trim()} />
              <Tooltip
                contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 8, fontSize: 12 }}
                formatter={(v: number) => [`${v}%`, "OEE"]}
              />
              <Bar dataKey="oee" fill="#25a368" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Live sensor strip */}
      {liveReadings.length > 0 && (
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-5">
          <p className="mb-4 text-sm font-semibold text-gray-200">Leituras ao Vivo</p>
          <div className="flex flex-wrap gap-4">
            {liveReadings.map((r) => (
              <div key={r.tag_id} className="rounded-lg border border-gray-700 bg-gray-800 px-4 py-3 min-w-[140px]">
                <p className="text-xs text-gray-500 truncate">{r.tag_name ?? r.tag_id}</p>
                <p className="mt-1 font-mono text-xl font-bold text-brand-400">
                  {r.value} <span className="text-sm text-gray-500">{r.unit}</span>
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Alerts + machine table */}
      <div className="grid gap-4 xl:grid-cols-3">
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-5">
          <p className="mb-4 text-sm font-semibold text-gray-200">Alertas Recentes</p>
          <div className="space-y-3">
            {events.slice(0, 5).map((alert) => (
              <div key={alert.id} className="rounded-lg border border-gray-800 bg-gray-950 p-3 space-y-1">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-xs font-semibold text-gray-200 truncate">{alert.rule_name}</p>
                  <SeverityBadge severity={alert.severity} />
                </div>
                <p className="text-xs text-gray-500 truncate">{alert.machine_name}</p>
                <p className="text-xs text-gray-400">{alert.message}</p>
                <p className="text-[10px] text-gray-600">
                  {format(new Date(alert.triggered_at), "HH:mm · dd/MM", { locale: ptBR })}
                  {alert.resolved_at && " · resolvido"}
                </p>
              </div>
            ))}
            {events.length === 0 && <p className="text-xs text-gray-600">Nenhum alerta.</p>}
          </div>
        </div>

        <div className="xl:col-span-2 rounded-xl border border-gray-800 bg-gray-900">
          <div className="flex items-center justify-between border-b border-gray-800 px-5 py-4">
            <p className="text-sm font-semibold text-gray-200">Máquinas</p>
            <span className="text-xs text-gray-500">{machines.length} total</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800">
                  {["Máquina", "Linha", "Status", "OEE", "Disponib.", "Desempenho", "Qualidade"].map((h) => (
                    <th key={h} className="px-4 py-3 text-left text-xs font-medium text-gray-500">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {machines.map((m, i) => (
                  <tr key={m.id} className={`border-b border-gray-800/60 hover:bg-gray-800/40 transition-colors ${i === machines.length - 1 ? "border-b-0" : ""}`}>
                    <td className="px-4 py-3 font-medium text-gray-200">{m.name}</td>
                    <td className="px-4 py-3 text-gray-400">{m.line_name}</td>
                    <td className="px-4 py-3"><MachineStatusBadge status={m.status} size="sm" /></td>
                    <OeeCell value={m.oee}          active={m.status === "running"} />
                    <OeeCell value={m.availability} active={m.status === "running"} />
                    <OeeCell value={m.performance}  active={m.status === "running"} />
                    <OeeCell value={m.quality}      active={m.status === "running"} />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

function OeeCell({ value, active }: { value?: number; active: boolean }) {
  if (!active || value == null) return <td className="px-4 py-3 text-gray-600 font-mono">—</td>;
  const color = value >= 85 ? "text-brand-400" : value >= 65 ? "text-yellow-400" : "text-red-400";
  return <td className={`px-4 py-3 font-mono font-medium ${color}`}>{value}%</td>;
}
