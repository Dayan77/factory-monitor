import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import clsx from "clsx";
import { inputClass } from "@/components/ui/Field";
import type { TreePlant } from "@/services/types";

/** URL-backed filter state (`?line=…&machine=…`) shared by the maintenance screens. */
export function useUrlFilters<K extends string>(keys: readonly K[]) {
  const [params, setParams] = useSearchParams();
  const values = useMemo(
    () => Object.fromEntries(keys.map((k) => [k, params.get(k) ?? ""])) as Record<K, string>,
    [params, keys],
  );
  function set(patch: Partial<Record<K | string, string | null>>, replace = true) {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v as string);
      else next.delete(k);
    }
    setParams(next, { replace });
  }
  return { values, set, params };
}

const selectClass = clsx(inputClass.replace("w-full ", ""), "w-full sm:w-auto sm:min-w-[11rem]");

/** Plant / line / machine selects driven by the plants tree. */
export function AssetFilters({ tree, plant, line, machine, onChange, showMachine = true }: {
  tree: TreePlant[];
  plant: string;
  line: string;
  machine: string;
  onChange: (patch: { plant?: string | null; line?: string | null; machine?: string | null }) => void;
  showMachine?: boolean;
}) {
  const plantObj = tree.find((p) => p.id === plant) ?? tree[0];
  const lines = plantObj?.lines ?? [];
  const machines = (line ? lines.filter((l) => l.id === line) : lines).flatMap((l) => l.machines);
  return (
    <>
      {tree.length > 1 && (
        <label className="w-full sm:w-auto">
          <span className="sr-only">Planta</span>
          <select aria-label="Planta" value={plantObj?.id ?? ""} onChange={(e) => onChange({ plant: e.target.value, line: null, machine: null })} className={selectClass}>
            {tree.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
      )}
      <select aria-label="Linha" value={line} onChange={(e) => onChange({ line: e.target.value || null, machine: null })} className={selectClass}>
        <option value="">Todas as linhas</option>
        {lines.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
      </select>
      {showMachine && (
        <select aria-label="Máquina" value={machine} onChange={(e) => onChange({ machine: e.target.value || null })} className={selectClass}>
          <option value="">Todas as máquinas</option>
          {machines.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select>
      )}
    </>
  );
}

export { selectClass };

/** Pill toggle group (aria-pressed). */
export function Pills<V extends string>({ label, items, value, onChange }: {
  label: string;
  items: readonly { value: V; label: string; count?: number }[];
  value: V;
  onChange: (v: V) => void;
}) {
  return (
    <div role="group" aria-label={label} className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
      {items.map((it) => (
        <button key={it.value} type="button" aria-pressed={value === it.value} onClick={() => onChange(it.value)}
          className={clsx("min-h-10 shrink-0 rounded-full border px-4 text-body font-medium transition-colors",
            value === it.value ? "border-accent bg-accent/15 text-ok" : "border-line bg-surface text-fg-muted hover:bg-surface-2")}>
          {it.label}
          {it.count !== undefined && <span className="ml-1.5 tabular text-fg-subtle">{it.count}</span>}
        </button>
      ))}
    </div>
  );
}
