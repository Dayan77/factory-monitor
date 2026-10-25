/**
 * Global search (command palette, Ctrl+K): pure matching/ranking so it is unit-tested apart from the UI.
 * Accent- and case-insensitive; every word typed must appear (in the label or in the keywords).
 */
export type CommandKind = "page" | "line" | "machine" | "position" | "work_order" | "action";

export interface CommandItem {
  id: string;
  kind: CommandKind;
  label: string;
  /** Secondary text (line of a machine, machine of a position, state of a WO…). */
  hint?: string;
  /** Extra searchable text (codes, synonyms). */
  keywords?: string;
  to: string;
}

export const KIND_LABEL: Record<CommandKind, string> = {
  action: "Ações",
  page: "Telas",
  line: "Linhas",
  machine: "Máquinas",
  position: "Posições",
  work_order: "Ordens de serviço",
};

const ORDER: CommandKind[] = ["action", "page", "line", "machine", "position", "work_order"];

export function fold(s: string): string {
  return s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
}

/** Score of an item for a query (higher is better), or null when it does not match. */
export function scoreItem(item: CommandItem, query: string): number | null {
  const q = fold(query);
  if (!q) return 0;
  const label = fold(item.label);
  const hay = `${label} ${fold(item.hint ?? "")} ${fold(item.keywords ?? "")}`;
  const words = q.split(/\s+/).filter(Boolean);
  if (!words.every((w) => hay.includes(w))) return null;
  let score = 0;
  if (label === q) score += 100;
  else if (label.startsWith(q)) score += 60;
  else if (label.split(/[\s\-/·]+/).some((part) => part.startsWith(words[0]))) score += 30;
  else if (label.includes(q)) score += 20;
  // shorter labels first among equals (an exact-ish hit beats a long sentence)
  score -= Math.min(label.length, 60) / 10;
  return score;
}

export interface CommandGroup {
  kind: CommandKind;
  label: string;
  items: CommandItem[];
}

/**
 * Groups the matches by kind (fixed order), at most `perGroup` each. With an empty query only actions and pages are
 * listed (the palette opens as a quick navigator); assets need at least one typed character.
 */
export function searchCommands(items: CommandItem[], query: string, perGroup = 6): CommandGroup[] {
  const empty = !fold(query);
  const scored: { item: CommandItem; score: number }[] = [];
  for (const item of items) {
    if (empty && item.kind !== "page" && item.kind !== "action") continue;
    const s = scoreItem(item, query);
    if (s !== null) scored.push({ item, score: s });
  }
  const groups = ORDER.map((kind) => {
    const list = scored.filter((x) => x.item.kind === kind);
    if (!empty) list.sort((a, b) => b.score - a.score || a.item.label.localeCompare(b.item.label, "pt-BR"));
    return { kind, label: KIND_LABEL[kind], best: list[0]?.score ?? -Infinity, items: list.slice(0, empty ? 12 : perGroup).map((x) => x.item) };
  }).filter((g) => g.items.length > 0);
  // with a query, the group holding the best match comes first ("tor" → Torno CNC before "Relatório de turno");
  // ties keep the fixed order (Array.prototype.sort is stable)
  if (!empty) groups.sort((a, b) => b.best - a.best);
  return groups.map(({ kind, label, items }) => ({ kind, label, items }));
}

/** Flattened list in display order (keyboard navigation over the groups). */
export function flatten(groups: CommandGroup[]): CommandItem[] {
  return groups.flatMap((g) => g.items);
}
