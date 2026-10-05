import type { TreeLine, TreeMachine, TreePlant } from "@/services/types";

export function findLine(tree: TreePlant[], lineId: string): { plant: TreePlant; line: TreeLine } | null {
  for (const plant of tree) {
    const line = plant.lines.find((l) => l.id === lineId);
    if (line) return { plant, line };
  }
  return null;
}

export function findMachine(
  tree: TreePlant[],
  machineId: string,
): { plant: TreePlant; line: TreeLine; machine: TreeMachine } | null {
  for (const plant of tree) {
    for (const line of plant.lines) {
      const machine = line.machines.find((m) => m.id === machineId);
      if (machine) return { plant, line, machine };
    }
  }
  return null;
}

/** Severity score for ordering: the most important problem first. */
export function lineSeverity(line: TreeLine): number {
  const s = line.status_summary;
  return s.error * 1000 + (s.maintenance ?? 0) * 100 + s.stopped * 10 + s.unknown;
}
