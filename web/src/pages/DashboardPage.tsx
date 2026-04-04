import { useMemo, useState, useEffect } from "react";
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
import {
  MOCK_MACHINES, MOCK_ALERTS, MOCK_LINES,
  generateOeeTrend, generateSensorHistory,
} from "@/services/mock";

const OEE_TREND   = generateOeeTrend();
const TEMP_SERIES = generateSensorHistory(245, "°C", 60);
const PRESS_SERIES = generateSensorHistory(6.4, "bar", 60);

function formatHour(iso: string) {
  return format(new Date(iso), "HH:mm", { locale: ptBR });
}

const CHART_STYLE = {
  fontSize: 11,
  fill: "#6b7280",
};

export default function DashboardPage() {
  const [tempData, setTempData] = useState(TEMP_SERIES);

  // Simulate live sensor updates every 10 s
  useEffect(() => {
    const id = setInterval(() => {
      setTempData((prev) => {
        const last = prev[prev.length - 1];
        const newVal = Math.max(200, Math.min(290, last.value + (Math.random() - 0.5) * 7));
        return [
          ...prev.slice(1),
          { ...last, value: +newVal.toFixed(1), timestamp: new Date().toISOString() },
        ];
      });
    }, 10_000);
    return () => clearInterval(id);
  }, []);

  const totalOee = useMemo(() => {
    const running = MOCK_MACHINES.filter((m) => m.status === "running");
    return running.length
      ? +(running.reduce((s, m) => s + m.oee, 0) / running.length).toFixed(1)
      : 0;
  }, []);

  const counts = useMemo(
    () => ({
      running:     MOCK_MACHINES.filter((m) => m.status === "running").length,
      stopped:     MOCK_MACHINES.filter((m) => m.status === "stopped").length,
      maintenance: MOCK_MACHINES.filter((m) => m.status === "maintenance").length,
      error:       MOCK_MACHINES.filter((m) => m.status === "error").length,
    }),
    []
  );

  const activeAlerts = MOCK_ALERTS.filter((a) => !a.resolved_at);

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
          trendValue="3.2% vs ontem"
          icon={
            <svg viewBox="0 0 20 20" fill="currentColor" className="size-5">
              <path d="M2 10a8 8 0 1 1 16 0A8 8 0 0 1 2 10zm8-3a1 1 0 0 0-1 1v2a1 1 0 0 0 .553.894l2 1a1 1 0 1 0 .894-1.789L11 9.618V8a1 1 0 0 0-1-1z" />
            </svg>
          }
        />
        <StatCard
          label="Máquinas Operando"
          value={`${counts.running} / ${MOCK_MACHINES.length}`}
          trend="neutral"
          trendValue="4 linhas ativas"
          icon={
            <svg viewBox="0 0 20 20" fill="currentColor" className="size-5">
              <path fillRule="evenodd" d="M6.267 3.455a3.066 3.066 0 0 0 .219-.08 3.066 3.066 0 0 0-2.659-5.33A3.066 3.066 0 0 0 2.19 3.03l-.008.03a3.066 3.066 0 0 0 2.62 3.982 3.066 3.066 0 0 0 3.374-3.457l-.001-.013A3.066 3.066 0 0 0 6.267 3.455z" clipRule="evenodd" />
            </svg>
          }
        />
        <StatCard
          label="Alertas Ativos"
          value={activeAlerts.length}
          trend={activeAlerts.length > 0 ? "down" : "neutral"}
          trendValue={activeAlerts.filter((a) => a.severity === "critical").length + " críticos"}
          icon={
            <svg viewBox="0 0 20 20" fill="currentColor" className="size-5">
              <path fillRule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 0 1 .75.75v3.5a.75.75 0 0 1-1.5 0v-3.5A.75.75 0 0 1 10 5zm0 9a1 1 0 1 0 0-2 1 1 0 0 0 0 2z" clipRule="evenodd" />
            </svg>
          }
        />
        <StatCard
          label="Disponibilidade"
          value="91.4"
          unit="%"
          trend="up"
          trendValue="1.8% esta semana"
          icon={
            <svg viewBox="0 0 20 20" fill="currentColor" className="size-5">
              <path fillRule="evenodd" d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16zm3.857-9.809a.75.75 0 0 0-1.214-.882l-3.483 4.79-1.88-1.88a.75.75 0 1 0-1.06 1.061l2.5 2.5a.75.75 0 0 0 1.137-.089l4-5.5z" clipRule="evenodd" />
            </svg>
          }
        />
      </div>

      {/* Charts row */}
      <div className="grid gap-4 xl:grid-cols-3">
        {/* OEE 24h trend */}
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
              <XAxis
                dataKey="timestamp"
                tickFormatter={formatHour}
                tick={CHART_STYLE}
                axisLine={false}
                tickLine={false}
                interval={3}
              />
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

        {/* OEE por linha */}
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-5">
          <p className="mb-4 text-sm font-semibold text-gray-200">OEE por Linha</p>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart
              data={MOCK_LINES}
              layout="vertical"
              margin={{ top: 0, right: 8, left: 0, bottom: 0 }}
            >
              <CartesianGrid stroke="#1f2937" horizontal={false} />
              <XAxis type="number" domain={[0, 100]} tick={CHART_STYLE} axisLine={false} tickLine={false} />
              <YAxis
                type="category"
                dataKey="name"
                width={90}
                tick={{ ...CHART_STYLE, fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(v: string) => v.split("—")[0].trim()}
              />
              <Tooltip
                contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 8, fontSize: 12 }}
                formatter={(v: number) => [`${v}%`, "OEE"]}
              />
              <Bar dataKey="oee" fill="#25a368" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Live sensor + alerts */}
      <div className="grid gap-4 xl:grid-cols-3">
        {/* Temperature live */}
        <div className="xl:col-span-2 rounded-xl border border-gray-800 bg-gray-900 p-5">
          <div className="mb-4 flex items-center justify-between">
            <p className="text-sm font-semibold text-gray-200">Temperatura — Forno Cura F-01</p>
            <span className="font-mono text-lg font-bold text-red-400">
              {tempData[tempData.length - 1].value} °C
            </span>
          </div>
          <ResponsiveContainer width="100%" height={180}>
            <AreaChart data={tempData} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="gradTemp" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="10%" stopColor="#ef4444" stopOpacity={0.25} />
                  <stop offset="95%" stopColor="#ef4444" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="#1f2937" vertical={false} />
              <XAxis dataKey="timestamp" tickFormatter={formatHour} tick={CHART_STYLE} axisLine={false} tickLine={false} interval={9} />
              <YAxis tick={CHART_STYLE} axisLine={false} tickLine={false} domain={["auto", "auto"]} />
              <Tooltip
                contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 8, fontSize: 12 }}
                labelFormatter={(v) => formatHour(v as string)}
                formatter={(v: number) => [`${v} °C`, "Temperatura"]}
              />
              <Area type="monotone" dataKey="value" stroke="#ef4444" fill="url(#gradTemp)" strokeWidth={2} dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        {/* Active alerts */}
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-5">
          <p className="mb-4 text-sm font-semibold text-gray-200">Alertas Recentes</p>
          <div className="space-y-3">
            {MOCK_ALERTS.map((alert) => (
              <div
                key={alert.id}
                className="rounded-lg border border-gray-800 bg-gray-950 p-3 space-y-1"
              >
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
          </div>
        </div>
      </div>

      {/* Machine table */}
      <div className="rounded-xl border border-gray-800 bg-gray-900">
        <div className="flex items-center justify-between border-b border-gray-800 px-5 py-4">
          <p className="text-sm font-semibold text-gray-200">Máquinas</p>
          <span className="text-xs text-gray-500">{MOCK_MACHINES.length} total</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800">
                {["Máquina", "Linha", "Status", "OEE", "Disponib.", "Desempenho", "Qualidade"].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium text-gray-500">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {MOCK_MACHINES.map((m, i) => (
                <tr
                  key={m.id}
                  className={`border-b border-gray-800/60 hover:bg-gray-800/40 transition-colors ${
                    i === MOCK_MACHINES.length - 1 ? "border-b-0" : ""
                  }`}
                >
                  <td className="px-4 py-3 font-medium text-gray-200">{m.name}</td>
                  <td className="px-4 py-3 text-gray-400">{m.line_name}</td>
                  <td className="px-4 py-3">
                    <MachineStatusBadge status={m.status} size="sm" />
                  </td>
                  <OeeCell value={m.oee} active={m.status === "running"} />
                  <OeeCell value={m.availability} active={m.status === "running"} />
                  <OeeCell value={m.performance} active={m.status === "running"} />
                  <OeeCell value={m.quality} active={m.status === "running"} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function OeeCell({ value, active }: { value: number; active: boolean }) {
  if (!active) return <td className="px-4 py-3 text-gray-600 font-mono">—</td>;
  const color =
    value >= 85 ? "text-brand-400"
    : value >= 65 ? "text-yellow-400"
    : "text-red-400";
  return (
    <td className={`px-4 py-3 font-mono font-medium ${color}`}>
      {value}%
    </td>
  );
}
