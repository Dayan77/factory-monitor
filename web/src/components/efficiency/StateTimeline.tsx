import { useMemo, useState } from "react";
import { createPortal } from "react-dom";
import clsx from "clsx";
import type { DowntimeEvent, ReasonCode } from "@/services/types";
import {
  buildTimeline, formatDuration, reasonLabel, TIMELINE_LABELS, type TimelineSegment, type TimelineState,
} from "@/utils/efficiency";

export interface TimelineRow {
  id: string;
  name: string;
  events: DowntimeEvent[];
}

interface Props {
  rows: TimelineRow[];
  start: number;
  stop: number;
  now: number;
  tz: string;
  reasons?: ReasonCode[];
  onSelect?: (ev: DowntimeEvent) => void;
}

export const STATE_CLASS: Record<TimelineState, string> = {
  running: "bg-ok-solid/80",
  stopped: "bg-warn-solid",
  error: "bg-danger-solid",
  planned: "bg-info-solid/70",
  nodata: "bg-[repeating-linear-gradient(135deg,rgb(var(--c-surface-2))_0_4px,rgb(var(--c-line-strong))_4px_6px)]",
};

const ORDER: TimelineState[] = ["running", "stopped", "error", "planned", "nodata"];

function ticks(start: number, stop: number): number[] {
  const span = stop - start;
  const steps = [3600e3, 2 * 3600e3, 3 * 3600e3, 6 * 3600e3, 12 * 3600e3, 86400e3, 2 * 86400e3, 7 * 86400e3];
  const step = steps.find((s) => span / s <= 8) ?? 7 * 86400e3;
  const out: number[] = [];
  for (let t = Math.ceil(start / step) * step; t < stop; t += step) out.push(t);
  return out;
}

interface Hover { seg: TimelineSegment; machine: string; x: number; y: number }

/**
 * Gantt-like state strip per machine: running / stopped / fault / planned / no data.
 * Segments with a stop are buttons (keyboard + touch) with a tooltip naming the reason.
 */
export default function StateTimeline({ rows, start, stop, now, tz, reasons, onSelect }: Props) {
  const [hover, setHover] = useState<Hover | null>(null);
  const span = Math.max(stop - start, 1);
  const multiDay = span > 36 * 3600e3;
  const fmt = useMemo(
    () => new Intl.DateTimeFormat("pt-BR", multiDay
      ? { timeZone: tz, day: "2-digit", month: "2-digit" }
      : { timeZone: tz, hour: "2-digit", minute: "2-digit" }),
    [tz, multiDay],
  );
  const fmtFull = useMemo(() => new Intl.DateTimeFormat("pt-BR", { timeZone: tz, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }), [tz]);
  const strips = useMemo(() => rows.map((r) => ({ ...r, segs: buildTimeline(r.events, start, stop, now) })), [rows, start, stop, now]);
  const tickList = ticks(start, stop);

  function describe(seg: TimelineSegment): string {
    const head = seg.event ? reasonLabel(seg.event.reason_code ?? seg.event.suggested_reason_code, reasons) : TIMELINE_LABELS[seg.state];
    return `${TIMELINE_LABELS[seg.state]} — ${head}, ${fmtFull.format(seg.start)} a ${fmtFull.format(seg.end)} (${formatDuration((seg.end - seg.start) / 1000)})`;
  }

  return (
    <div className="relative">
      <ul className="mb-3 flex flex-wrap gap-x-4 gap-y-1.5 text-caption text-fg-muted" aria-label="Legenda">
        {ORDER.map((s) => (
          <li key={s} className="flex items-center gap-1.5">
            <span aria-hidden="true" className={clsx("size-3 rounded-sm", STATE_CLASS[s])} />
            {TIMELINE_LABELS[s]}
          </li>
        ))}
      </ul>
      <div className="space-y-2" role="list" aria-label="Estados das máquinas">
        {strips.map((r) => (
          <div key={r.id} role="listitem" className="grid grid-cols-[minmax(0,7rem)_1fr] items-center gap-3 sm:grid-cols-[minmax(0,11rem)_1fr]">
            <p className="truncate text-caption font-medium text-fg" title={r.name}>{r.name}</p>
            <div className="relative h-8 overflow-hidden rounded-md bg-surface-2" data-testid="timeline-strip">
              {r.segs.map((seg, i) => {
                const left = ((seg.start - start) / span) * 100;
                const width = ((seg.end - seg.start) / span) * 100;
                const common = {
                  className: clsx("absolute inset-y-0 border-r border-surface/60", STATE_CLASS[seg.state],
                    seg.event && "cursor-pointer hover:brightness-110 focus-visible:z-10 focus-visible:outline-offset-0"),
                  style: { left: `${left}%`, width: `max(${width}%, 2px)` },
                  onMouseMove: (e: React.MouseEvent) => setHover({ seg, machine: r.name, x: e.clientX, y: e.clientY }),
                  onMouseLeave: () => setHover(null),
                };
                return seg.event ? (
                  <button
                    key={i}
                    type="button"
                    data-state={seg.state}
                    aria-label={`${r.name}: ${describe(seg)}`}
                    onFocus={(e) => {
                      const b = e.currentTarget.getBoundingClientRect();
                      setHover({ seg, machine: r.name, x: b.left + b.width / 2, y: b.bottom });
                    }}
                    onBlur={() => setHover(null)}
                    onClick={() => seg.event && onSelect?.(seg.event)}
                    {...common}
                  />
                ) : (
                  <div key={i} data-state={seg.state} {...common} />
                );
              })}
            </div>
          </div>
        ))}
        <div className="grid grid-cols-[minmax(0,7rem)_1fr] gap-3 sm:grid-cols-[minmax(0,11rem)_1fr]" aria-hidden="true">
          <span />
          <div className="relative h-4 text-[10px] text-fg-subtle">
            {tickList.map((t) => (
              <span key={t} className="absolute -translate-x-1/2 tabular" style={{ left: `${((t - start) / span) * 100}%` }}>
                {fmt.format(t)}
              </span>
            ))}
          </div>
        </div>
      </div>
      {hover && createPortal(
        <div
          role="tooltip"
          className="pointer-events-none fixed z-50 max-w-xs rounded-lg border border-line bg-surface px-3 py-2 text-caption shadow-pop"
          style={{ left: Math.min(hover.x + 12, window.innerWidth - 300), top: hover.y + 12 }}
        >
          <p className="font-medium text-fg">{hover.machine}</p>
          <p className="flex items-center gap-1.5 text-fg-muted">
            <span aria-hidden="true" className={clsx("size-2 rounded-sm", STATE_CLASS[hover.seg.state])} />
            {TIMELINE_LABELS[hover.seg.state]}
          </p>
          {hover.seg.event && (
            <p className="mt-1 text-fg">
              {reasonLabel(hover.seg.event.reason_code, reasons)}
              {!hover.seg.event.reason_code && hover.seg.event.suggested_reason_code && (
                <span className="text-fg-subtle"> · sugerido: {reasonLabel(hover.seg.event.suggested_reason_code, reasons)}</span>
              )}
              {hover.seg.event.fault_code != null && <span className="text-fg-subtle"> · código {hover.seg.event.fault_code}</span>}
            </p>
          )}
          <p className="mt-1 tabular text-fg-subtle">
            {fmtFull.format(hover.seg.start)} → {fmtFull.format(hover.seg.end)} · {formatDuration((hover.seg.end - hover.seg.start) / 1000)}
          </p>
        </div>,
        document.body,
      )}
    </div>
  );
}
