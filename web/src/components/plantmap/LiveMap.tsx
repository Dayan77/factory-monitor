import { useCallback, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import type { LayoutMarker, MapView } from "@/services/sprint6Types";
import { machineIndex, mapAspect, markerKey } from "@/utils/plantMap";
import { markerText, STATE_META, STATE_ORDER } from "@/utils/visualState";
import { formatDec1, formatPct } from "@/utils/format";
import { badgeText, oeeText } from "@/utils/mapKpis";
import MapSurface from "./MapSurface";
import { AreaLabelMarker, LineLabelMarker, MachineMarker } from "./Markers";
import StateIcon from "./StateIcon";

interface Props {
  view: MapView;
  imageUrl: string | null;
  now: number;
  dimmed?: boolean;
  selectedId?: string | null;
  onSelect?: (machineId: string) => void;
  onOpenLine?: (lineId: string) => void;
  minWidth?: number;
  className?: string;
}

export function viewMarkers(view: MapView): LayoutMarker[] {
  return view.kind === "published" ? view.layout.markers : view.markers;
}

/** The live map: floor plan (or automatic grid) + markers + hover tooltip. */
export default function LiveMap({ view, imageUrl, now, dimmed, selectedId, onSelect, onOpenLine, minWidth, className }: Props) {
  const [zoom, setZoom] = useState(1);
  const [hover, setHover] = useState<{ id: string; rect: DOMRect; box: DOMRect } | null>(null);
  const inner = useRef<HTMLDivElement>(null);
  const machines = useMemo(() => machineIndex(view), [view]);
  const lines = useMemo(() => new Map(view.lines.map((l) => [l.line_id, l])), [view.lines]);
  const markers = viewMarkers(view);
  const aspect = view.kind === "published" ? mapAspect(view.layout.image_width_px, view.layout.image_height_px) : mapAspect(null, null, view.lanes.length);

  const onHover = useCallback((id: string | null, el: HTMLElement | null) => {
    const box = inner.current?.getBoundingClientRect();
    setHover(id && el && box ? { id, rect: el.getBoundingClientRect(), box } : null);
  }, []);

  const hovered = hover ? machines.get(hover.id) : undefined;
  return (
    <div className={clsx("relative", className)}>
      <MapSurface ref={inner} aspect={aspect} imageUrl={imageUrl} zoom={zoom} onZoom={setZoom} dragToPan dimmed={dimmed}
        minWidth={minWidth} label={`Mapa da planta ${view.plant_name}`}
        lanes={view.kind === "auto" ? view.lanes.map((l) => ({ key: l.line_id, y: l.y, name: l.name })) : undefined}>
        {markers.map((m, i) => {
          const key = markerKey(m, i);
          if (m.kind === "machine" && m.machine_id) {
            const mv = machines.get(m.machine_id);
            return mv ? <MachineMarker key={key} marker={m} machine={mv} now={now} selected={selectedId === mv.machine_id} onSelect={onSelect} onHover={onHover} /> : null;
          }
          if (m.kind === "line_label" && m.line_id) {
            // automatic layout: the line label sits at the top-left of its lane, clear of the machines
            const lane = view.kind === "auto" ? view.lanes.find((l) => l.line_id === m.line_id) : undefined;
            const half = view.kind === "auto" ? 0.5 / Math.max(1, view.lanes.length) : 0;
            const mk = lane ? { ...m, x: 0.012, y: lane.y - half + Math.min(0.08, half * 0.3) } : m;
            return <LineLabelMarker key={key} marker={mk} anchor={lane ? "left" : "center"} line={lines.get(m.line_id)} onOpen={onOpenLine} />;
          }
          if (m.kind === "area_label") return <AreaLabelMarker key={key} marker={m} />;
          return null;
        })}
        {hovered && hover && (
          <div role="tooltip" data-testid="map-tooltip"
            className="pointer-events-none absolute z-30 w-60 -translate-x-1/2 rounded-lg border border-line bg-surface p-3 text-caption shadow-pop"
            style={{
              left: hover.rect.left + hover.rect.width / 2 - hover.box.left,
              top: hover.rect.bottom - hover.box.top + 8,
            }}>
            <p className="font-semibold text-fg">{hovered.name}</p>
            <p className="mt-0.5 flex items-center gap-1 text-fg-muted"><StateIcon state={hovered.state} className="size-3.5" />{markerText(hovered, now)}</p>
            <p className="mt-1 tabular text-fg-muted">
              OEE do turno {hovered.kpis?.oee == null ? "—" : formatPct(hovered.kpis.oee)}
              {hovered.kpis?.cadence_per_hour != null && ` · ao vivo ${formatDec1(hovered.kpis.cadence_per_hour)} pç/h`}
            </p>
            {oeeText(hovered.kpis).suspect && <p className="mt-1 text-[#b45309]">⚠ Desempenho acima de 100 %: valor suspeito</p>}
            {hovered.badges.map(badgeText).filter(Boolean).map((t) => <p key={t} className="mt-1 text-fg-muted">ⓘ {t}</p>)}
            <p className="mt-1 text-fg-subtle">Clique para ver o resumo</p>
          </div>
        )}
      </MapSurface>
    </div>
  );
}

/** Legend: every state with colour + icon + text (never colour only). */
export function StateLegend({ counts }: { counts?: Partial<Record<string, number>> }) {
  return (
    <ul aria-label="Legenda dos estados" className="flex flex-wrap gap-1.5">
      {STATE_ORDER.map((s) => (
        <li key={s} className={clsx("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-caption font-medium", STATE_META[s].marker)}>
          <StateIcon state={s} className="size-3.5" />
          {STATE_META[s].label}
          {counts && <span className="tabular opacity-90">· {counts[s] ?? 0}</span>}
        </li>
      ))}
    </ul>
  );
}
