import { MOCK_LINES, MOCK_MACHINES } from "@/services/mock";
import MachineStatusBadge from "@/components/MachineStatusBadge";
import clsx from "clsx";

export default function LinesPage() {
  return (
    <div className="space-y-4">
      {MOCK_LINES.map((line) => {
        const machines = MOCK_MACHINES.filter((m) => m.line_id === line.id);
        return (
          <div key={line.id} className="rounded-xl border border-gray-800 bg-gray-900">
            {/* Line header */}
            <div className="flex items-center justify-between border-b border-gray-800 px-5 py-4">
              <div className="flex items-center gap-3">
                <span
                  className={clsx(
                    "size-2.5 rounded-full",
                    line.is_active ? "bg-brand-400 animate-pulse" : "bg-gray-600"
                  )}
                />
                <div>
                  <p className="font-semibold text-gray-100">{line.name}</p>
                  <p className="text-xs text-gray-500">{line.location}</p>
                </div>
              </div>
              <div className="flex items-center gap-6 text-sm">
                <div className="text-right">
                  <p className="text-xs text-gray-500">Máquinas</p>
                  <p className="font-mono font-medium text-gray-200">
                    {line.running_count}/{line.machine_count}
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-xs text-gray-500">OEE</p>
                  <p
                    className={clsx(
                      "font-mono font-bold text-lg",
                      line.oee >= 85 ? "text-brand-400"
                      : line.oee >= 65 ? "text-yellow-400"
                      : line.oee > 0 ? "text-red-400"
                      : "text-gray-600"
                    )}
                  >
                    {line.oee > 0 ? `${line.oee}%` : "—"}
                  </p>
                </div>
              </div>
            </div>

            {/* Machines in this line */}
            {machines.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-800/60">
                      {["Máquina", "Modelo", "Status", "OEE", "Disponib.", "Desempenho", "Qualidade"].map((h) => (
                        <th key={h} className="px-4 py-2.5 text-left text-xs text-gray-500 font-medium">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {machines.map((m, i) => (
                      <tr
                        key={m.id}
                        className={clsx(
                          "hover:bg-gray-800/30 transition-colors",
                          i < machines.length - 1 && "border-b border-gray-800/40"
                        )}
                      >
                        <td className="px-4 py-3 font-medium text-gray-200">{m.name}</td>
                        <td className="px-4 py-3 text-gray-500">{m.model}</td>
                        <td className="px-4 py-3">
                          <MachineStatusBadge status={m.status} size="sm" />
                        </td>
                        {[m.oee, m.availability, m.performance, m.quality].map((v, j) => (
                          <td key={j} className="px-4 py-3">
                            {m.status === "running" ? (
                              <span
                                className={clsx(
                                  "font-mono font-medium",
                                  v >= 85 ? "text-brand-400" : v >= 65 ? "text-yellow-400" : "text-red-400"
                                )}
                              >
                                {v}%
                              </span>
                            ) : (
                              <span className="font-mono text-gray-600">—</span>
                            )}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="px-5 py-4 text-sm text-gray-600">Nenhuma máquina cadastrada.</p>
            )}
          </div>
        );
      })}
    </div>
  );
}
