import { useState } from "react";
import MachineStatusBadge from "@/components/MachineStatusBadge";
import { MOCK_MACHINES, MOCK_LINES } from "@/services/mock";
import type { MachineStatus } from "@/services/types";
import clsx from "clsx";

const STATUS_FILTERS: { label: string; value: MachineStatus | "all" }[] = [
  { label: "Todas", value: "all" },
  { label: "Operando", value: "running" },
  { label: "Parada", value: "stopped" },
  { label: "Manutenção", value: "maintenance" },
  { label: "Falha", value: "error" },
];

export default function MachinesPage() {
  const [lineFilter, setLineFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState<MachineStatus | "all">("all");

  const machines = MOCK_MACHINES.filter((m) => {
    const lineOk   = lineFilter === "all"   || m.line_id === lineFilter;
    const statusOk = statusFilter === "all" || m.status === statusFilter;
    return lineOk && statusOk;
  });

  return (
    <div className="space-y-5">
      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <select
          value={lineFilter}
          onChange={(e) => setLineFilter(e.target.value)}
          className="rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200 focus:outline-none focus:ring-1 focus:ring-brand-500"
        >
          <option value="all">Todas as linhas</option>
          {MOCK_LINES.map((l) => (
            <option key={l.id} value={l.id}>{l.name}</option>
          ))}
        </select>

        <div className="flex rounded-lg border border-gray-700 overflow-hidden">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setStatusFilter(f.value)}
              className={clsx(
                "px-3 py-2 text-xs font-medium transition-colors",
                statusFilter === f.value
                  ? "bg-brand-500 text-white"
                  : "bg-gray-800 text-gray-400 hover:bg-gray-700"
              )}
            >
              {f.label}
            </button>
          ))}
        </div>

        <span className="ml-auto text-xs text-gray-500">{machines.length} máquinas</span>
      </div>

      {/* Cards grid */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {machines.map((m) => (
          <div
            key={m.id}
            className={clsx(
              "rounded-xl border p-5 space-y-4",
              m.status === "error"
                ? "border-red-500/30 bg-red-500/5"
                : m.status === "maintenance"
                ? "border-yellow-500/20 bg-yellow-500/5"
                : "border-gray-800 bg-gray-900"
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <div>
                <p className="font-semibold text-gray-100">{m.name}</p>
                <p className="text-xs text-gray-500">{m.model}</p>
              </div>
              <MachineStatusBadge status={m.status} />
            </div>

            <p className="text-xs text-gray-500">{m.line_name}</p>

            {m.status === "running" ? (
              <div className="grid grid-cols-4 gap-2">
                {[
                  { label: "OEE",       value: m.oee },
                  { label: "Disponib.", value: m.availability },
                  { label: "Desemp.",   value: m.performance },
                  { label: "Qualid.",   value: m.quality },
                ].map(({ label, value }) => (
                  <div key={label} className="text-center">
                    <p
                      className={clsx(
                        "text-lg font-bold font-mono",
                        value >= 85 ? "text-brand-400" : value >= 65 ? "text-yellow-400" : "text-red-400"
                      )}
                    >
                      {value}
                    </p>
                    <p className="text-[10px] text-gray-600">{label}</p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-gray-600 italic">
                {m.status === "maintenance" ? "Em manutenção preventiva." : m.status === "error" ? "Verificar falha no equipamento." : "Máquina desligada."}
              </p>
            )}

            <OeeBar value={m.oee} active={m.status === "running"} />
          </div>
        ))}
      </div>
    </div>
  );
}

function OeeBar({ value, active }: { value: number; active: boolean }) {
  if (!active) return null;
  const color =
    value >= 85 ? "bg-brand-500"
    : value >= 65 ? "bg-yellow-500"
    : "bg-red-500";
  return (
    <div className="h-1.5 w-full rounded-full bg-gray-800">
      <div
        className={clsx("h-full rounded-full transition-all duration-700", color)}
        style={{ width: `${value}%` }}
      />
    </div>
  );
}
