/**
 * Deep links of the AI assistant citations (docs/ai_assistant.md §4 catalog `link` templates, IA-2): the server
 * cites screens with API-style params (`start`/`stop` ISO UTC, `group_by`, `line_id`, …). The screens keep their
 * own URL state (`period=custom&from&to`, `group`, `line`, …). This pure translation runs once on navigation so a
 * citation opens the screen with the same filters that produced the number. Unknown params are kept.
 */

function localDate(ms: number, tz: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(ms));
}

/** ISO window → plant-local inclusive day range (`stop` is exclusive). */
export function isoWindowToDays(start: string, stop: string | null, tz: string): { from: string; to: string } | null {
  const s = Date.parse(start);
  if (Number.isNaN(s)) return null;
  const e = stop ? Date.parse(stop) : NaN;
  const from = localDate(s, tz);
  const to = Number.isNaN(e) || e <= s ? from : localDate(e - 1, tz);
  return { from, to };
}

const OEE_GROUPS: Record<string, string | null> = { hour: "hour", shift: "shift", shift_name: "shift", day: "day", none: null };

/** Returns the rewritten params, or null when nothing needs to change. */
export function normalizeCatalogParams(pathname: string, input: URLSearchParams, tz: string): URLSearchParams | null {
  const p = new URLSearchParams(input);
  let changed = false;
  const move = (from: string, to: string) => {
    const v = p.get(from);
    if (v === null) return;
    p.delete(from);
    changed = true;
    if (v && !p.get(to)) p.set(to, v);
  };
  const window = () => {
    const start = p.get("start");
    if (start === null) return null;
    const w = isoWindowToDays(start, p.get("stop"), tz);
    p.delete("start");
    p.delete("stop");
    changed = true;
    return w;
  };

  if (pathname === "/oee") {
    const w = window();
    if (w) { p.set("period", "custom"); p.set("from", w.from); p.set("to", w.to); }
    const g = p.get("group_by");
    if (g !== null) {
      p.delete("group_by");
      changed = true;
      const mapped = OEE_GROUPS[g];
      if (mapped) p.set("group", mapped);
    }
  } else if (pathname === "/stops") {
    move("line_id", "line");
    if (p.has("machine_id")) { p.delete("machine_id"); changed = true; }
    window();
    if (p.has("unjustified")) { p.delete("unjustified"); changed = true; }   // the screen lists unjustified stops
  } else if (pathname === "/reports/shift") {
    move("line_id", "line");
    move("shift_id", "shift");
  } else if (pathname === "/maintenance/kpis") {
    const start = p.get("start");
    if (start !== null) {
      const w = window();
      if (w) p.set("month", w.from.slice(0, 7));
    }
  } else if (pathname === "/maintenance/reliability") {
    move("group_by", "group");
  } else if (pathname === "/maintenance/work-orders") {
    move("assignee_id", "assignee");
    const o = p.get("overdue");
    if (o !== null) { p.delete("overdue"); changed = true; if (o === "true") p.set("quick", "overdue"); }
    move("machine_id", "machine");
    move("line_id", "line");
  } else if (pathname === "/maintenance/failures") {
    move("machine_id", "machine");
    move("line_id", "line");
    if (p.has("position_id")) { p.delete("position_id"); changed = true; }
  } else if (pathname === "/factory") {
    if (p.has("plant_id")) { p.delete("plant_id"); changed = true; }
  }
  return changed ? p : null;
}
