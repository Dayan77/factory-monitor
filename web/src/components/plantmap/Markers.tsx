import { memo, useEffect, useRef, useState } from "react";
import clsx from "clsx";
import type { LayoutMarker, LineVisual, MachineVisual, MarkerSize } from "@/services/sprint6Types";
import { SUSPECT_TITLE, badgeText, oeeText } from "@/utils/mapKpis";
import { STATE_META, markerText } from "@/utils/visualState";
import { formatDec1 } from "@/utils/format";
import StateIcon from "./StateIcon";

const BOX: Record<MarkerSize, string> = {
  s: "min-w-[5.5rem] max-w-[8rem] px-2 py-1",
  m: "w-[9.5rem] px-2.5 py-1.5",
  l: "w-[12rem] px-3 py-2",
};

/** Bumps a key whenever `state` changes after the first render (drives the pulse ring). */
function useChangeKey(state: string): number {
  const prev = useRef(state);
  const [key, setKey] = useState(0);
  useEffect(() => {
    if (prev.current !== state) {
      prev.current = state;
      setKey((k) => k + 1);
    }
  }, [state]);
  return key;
}

function position(m: Pick<LayoutMarker, "x" | "y" | "rotation_deg">, anchor: "center" | "left" = "center") {
  return {
    left: `${m.x * 100}%`,
    top: `${m.y * 100}%`,
    transform: `translate(${anchor === "left" ? "0" : "-50%"}, -50%) rotate(${m.rotation_deg || 0}deg)`,
  };
}

const SEV_TONE: Record<string, string> = {
  critical: "bg-[#b91c1c] text-white",
  warning: "bg-[#f59e0b] text-[#111827]",
  watch: "bg-[#1d4ed8] text-white",
};

interface MachineMarkerProps {
  marker: LayoutMarker;
  machine: MachineVisual;
  now: number;
  selected?: boolean;
  onSelect?: (id: string) => void;
  onHover?: (id: string | null, el: HTMLElement | null) => void;
}

function SuspectMark() {
  return <span aria-hidden="true" title={SUSPECT_TITLE} data-testid="oee-suspect" className="ml-0.5 font-bold text-[#b45309]">⚠</span>;
}

/** A machine on the map: colour + icon + text of its state (§1.2), duration, OEE and cadence for m/l (§1.4). */
export const MachineMarker = memo(function MachineMarker({ marker, machine, now, selected, onSelect, onHover }: MachineMarkerProps) {
  const meta = STATE_META[machine.state];
  const pulse = useChangeKey(machine.state);
  const size = marker.size ?? "m";
  const text = markerText(machine, now);
  const pred = machine.badges.find((b) => b.kind === "predictive");
  const extra = machine.badges.filter((b) => b.kind === "data_coverage" || b.kind === "outside_calendar");
  const oee = oeeText(machine.kpis);
  return (
    <button
      type="button"
      data-marker
      data-testid="map-marker"
      data-machine-id={machine.machine_id}
      data-state={machine.state}
      aria-pressed={selected || undefined}
      onClick={() => onSelect?.(machine.machine_id)}
      onMouseEnter={(e) => onHover?.(machine.machine_id, e.currentTarget)}
      onMouseLeave={() => onHover?.(null, null)}
      onFocus={(e) => onHover?.(machine.machine_id, e.currentTarget)}
      onBlur={() => onHover?.(null, null)}
      style={position(marker)}
      className={clsx(
        "absolute z-10 rounded-lg border-2 text-left shadow-card transition-colors duration-500",
        "focus-visible:z-20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        meta.marker, BOX[size], selected && "z-20 ring-4 ring-accent/60",
      )}
    >
      {pulse > 0 && <span key={pulse} aria-hidden="true" className="pointer-events-none absolute inset-0 animate-marker-pulse rounded-lg" />}
      {pred && (
        <span title={`Alerta preditivo (${pred.severity})`}
          className={clsx("absolute -right-2 -top-2 flex size-5 items-center justify-center rounded-full border-2 border-surface text-[10px] font-bold", SEV_TONE[pred.severity ?? "watch"] ?? SEV_TONE.watch)}>
          !
        </span>
      )}
      {extra.length > 0 && (
        <span data-testid="map-badge-data" title={extra.map(badgeText).join(" · ")}
          className="absolute -left-2 -top-2 flex size-5 items-center justify-center rounded-full border-2 border-surface bg-[#475569] text-[10px] font-bold text-white">
          i
        </span>
      )}
      <span className="flex min-w-0 items-center gap-1.5">
        <StateIcon state={machine.state} className={size === "s" ? "size-3.5" : "size-4"} />
        <span className={clsx("truncate font-semibold", size === "s" ? "text-[11px] leading-4" : "text-caption")}>{machine.name}<span className="sr-only">:</span></span>
      </span>
      {/* accessible name = the visible text + what only colour/icons show (WCAG 2.5.3: the name contains the label) */}
      {size === "s" && <span className="sr-only"> {text} · {oee.text}</span>}
      {pred && <span className="sr-only"> — alerta preditivo ({pred.severity ?? "observação"})</span>}
      {oee.suspect && <span className="sr-only"> — OEE suspeito (desempenho acima de 100 %)</span>}
      {extra.length > 0 && <span className="sr-only">{extra.map((b) => ` — ${badgeText(b)}`).join("")}</span>}
      {size !== "s" && (
        <>
          <span className="mt-0.5 block truncate text-[11px] leading-4 opacity-95">{text}</span>
          <span className="mt-0.5 flex items-center justify-between gap-2 text-[11px] font-medium leading-4 tabular opacity-90">
            <span>{oee.text}{oee.suspect && <SuspectMark />}</span>
            {size === "l" || machine.kpis?.cadence_per_hour != null ? (
              <span title="Cadência ao vivo: peças dos últimos 15 min × 4">{machine.kpis?.cadence_per_hour == null ? "— pç/h" : `${formatDec1(machine.kpis.cadence_per_hour)} pç/h`}</span>
            ) : null}
          </span>
        </>
      )}
    </button>
  );
});

/** Line label: line name, OEE of the current shift from its output machine, and the output machine's state. */
export const LineLabelMarker = memo(function LineLabelMarker({ marker, line, onOpen, anchor = "center" }: { marker: LayoutMarker; line: LineVisual | undefined; onOpen?: (lineId: string) => void; anchor?: "center" | "left" }) {
  if (!line) return null;
  const st = line.output_state;
  const oee = oeeText(line.kpis);
  return (
    <button
      type="button"
      data-marker
      data-testid="map-line-label"
      onClick={() => onOpen?.(line.line_id)}
      style={position(marker, anchor)}
      className="absolute z-10 flex items-center gap-2 rounded-full border border-line-strong bg-surface/95 px-3 py-1 text-caption shadow-card hover:border-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
    >
      <span className="font-semibold text-fg"><span className="sr-only">Linha </span>{line.name}</span>
      <span className="tabular text-fg-muted"><span className="sr-only">: OEE do turno </span>{oee.text}{oee.suspect && <SuspectMark />}{oee.suspect && <span className="sr-only"> (suspeito: desempenho acima de 100 %)</span>}</span>
      {st && (
        <span className={clsx("inline-flex items-center gap-1 rounded-full border px-1.5 py-px text-[10px] font-semibold", STATE_META[st].marker)} title={`Máquina de saída: ${line.output_text ?? STATE_META[st].label}`}>
          <StateIcon state={st} className="size-3" />
          saída
        </span>
      )}
    </button>
  );
});

export function AreaLabelMarker({ marker }: { marker: LayoutMarker }) {
  return (
    <span data-marker style={position(marker)}
      className="pointer-events-none absolute rounded-md border border-dashed border-line-strong bg-surface/80 px-2 py-0.5 text-caption font-medium uppercase tracking-wide text-fg-muted">
      {marker.label}
    </span>
  );
}
