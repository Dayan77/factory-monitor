import { useMemo } from "react";
import { useMachines, useTags } from "@/hooks/useQueries";
import { useRealtimeStore } from "@/store/realtime";
import type { Tag } from "@/services/types";

/** Live readings from the WebSocket, labelled with sensor + machine names (never raw UUIDs). */
export default function LiveReadings() {
  const readings = useRealtimeStore((s) => s.readings);
  const tagsQ = useTags();
  const machinesQ = useMachines();

  const tagById = useMemo(() => new Map<string, Tag>((tagsQ.data ?? []).map((t) => [t.id, t])), [tagsQ.data]);
  const machineName = useMemo(
    () => new Map((machinesQ.data ?? []).map((m) => [m.id, m.name])),
    [machinesQ.data],
  );

  const list = Object.values(readings);
  if (list.length === 0) return null;

  const items = list
    .map((r) => {
      const tag = tagById.get(r.tag_id);
      return {
        reading: r,
        tagName: tag?.name ?? (tagsQ.isPending ? "Carregando…" : "Sensor não cadastrado"),
        machine: tag?.machine_id ? machineName.get(tag.machine_id) ?? "—" : "Sem máquina",
        unit: r.unit ?? tag?.unit ?? "",
      };
    })
    .sort((a, b) => a.machine.localeCompare(b.machine, "pt-BR") || a.tagName.localeCompare(b.tagName, "pt-BR"));

  return (
    <section className="rounded-xl border border-gray-800 bg-gray-900 p-5" aria-labelledby="dash-live">
      <h2 id="dash-live" className="mb-4 text-sm font-semibold text-gray-200">Leituras ao Vivo</h2>
      <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6">
        {items.map(({ reading, tagName, machine, unit }) => (
          <li key={reading.tag_id} className="min-w-0 rounded-lg border border-gray-700 bg-gray-800 px-4 py-3">
            <p className="truncate text-xs font-medium text-gray-300" title={tagName}>{tagName}</p>
            <p className="truncate text-[11px] text-gray-500" title={machine}>{machine}</p>
            <p className="mt-1 font-mono text-xl font-bold tabular-nums text-brand-400">
              {reading.value.toLocaleString("pt-BR", { maximumFractionDigits: 2 })}{" "}
              <span className="text-sm text-gray-500">{unit}</span>
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}
