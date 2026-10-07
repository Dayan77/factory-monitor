import { forwardRef, useCallback, useEffect, useRef, type ReactNode } from "react";
import clsx from "clsx";

export const ZOOM_MIN = 1;
export const ZOOM_MAX = 4;

interface Props {
  /** width / height of the floor plan (image) or of the automatic grid. */
  aspect: number;
  /** Object URL of the floor-plan image (fetched with the JWT). */
  imageUrl?: string | null;
  /** Automatic layout: one lane per line (normalized y of each lane centre). */
  lanes?: { key: string; y: number; name: string }[];
  zoom: number;
  onZoom: (z: number) => void;
  /** Viewer: drag the background to pan (editor: off — the background is where markers are dropped). */
  dragToPan?: boolean;
  dimmed?: boolean;
  children: ReactNode;
  className?: string;
  /** Minimum width of the map at zoom 1 (px): on a phone the map scrolls inside its frame, never the page. */
  minWidth?: number;
  label: string;
}

/**
 * Pan / zoom frame of the plant map. Markers are positioned in % of the inner box (normalized coordinates, centre of
 * the marker), so they stay in place at any zoom or screen size; their own size is fixed in px (readable at any
 * zoom). Zooming changes the inner width (no scaled text); panning is the frame's native scroll (smooth with 200
 * markers) plus drag-to-pan on the background.
 */
const MapSurface = forwardRef<HTMLDivElement, Props>(function MapSurface(
  { aspect, imageUrl, lanes, zoom, onZoom, dragToPan = false, dimmed = false, children, className, minWidth = 720, label },
  innerRef,
) {
  const frame = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; sl: number; st: number; id: number } | null>(null);

  const zoomAt = useCallback((next: number, cx?: number, cy?: number) => {
    const el = frame.current;
    const z = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(next * 100) / 100));
    if (!el || z === zoom) {
      onZoom(z);
      return;
    }
    // keep the point under the cursor (or the centre) in place
    const rect = el.getBoundingClientRect();
    const px = (cx ?? rect.left + rect.width / 2) - rect.left;
    const py = (cy ?? rect.top + rect.height / 2) - rect.top;
    const fx = (el.scrollLeft + px) / Math.max(1, el.scrollWidth);
    const fy = (el.scrollTop + py) / Math.max(1, el.scrollHeight);
    onZoom(z);
    requestAnimationFrame(() => {
      el.scrollLeft = fx * el.scrollWidth - px;
      el.scrollTop = fy * el.scrollHeight - py;
    });
  }, [zoom, onZoom]);

  useEffect(() => {
    const el = frame.current;
    if (!el) return;
    function onWheel(e: WheelEvent) {
      if (!e.ctrlKey && !e.metaKey) return;          // plain wheel scrolls; ctrl/pinch zooms
      e.preventDefault();
      zoomAt(zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15), e.clientX, e.clientY);
    }
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoom, zoomAt]);

  return (
    <div className={clsx("relative overflow-hidden rounded-xl border border-line bg-surface-2/40", className)}>
      <div
        ref={frame}
        role="region"
        aria-label={label}
        tabIndex={0}
        data-testid="map-frame"
        className={clsx("max-h-[78vh] overflow-auto overscroll-contain focus-visible:outline-offset-[-2px]", dragToPan && "cursor-grab active:cursor-grabbing")}
        onKeyDown={(e) => {
          if (e.key === "+" || e.key === "=") { e.preventDefault(); zoomAt(zoom * 1.25); }
          if (e.key === "-") { e.preventDefault(); zoomAt(zoom / 1.25); }
          if (e.key === "0") { e.preventDefault(); zoomAt(1); }
        }}
        onPointerDown={(e) => {
          if (!dragToPan || e.button !== 0 || (e.target as HTMLElement).closest("[data-marker]")) return;
          const el = frame.current;
          if (!el) return;
          drag.current = { x: e.clientX, y: e.clientY, sl: el.scrollLeft, st: el.scrollTop, id: e.pointerId };
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          const el = frame.current;
          if (!d || !el || d.id !== e.pointerId) return;
          el.scrollLeft = d.sl - (e.clientX - d.x);
          el.scrollTop = d.st - (e.clientY - d.y);
        }}
        onPointerUp={() => { drag.current = null; }}
        onPointerCancel={() => { drag.current = null; }}
      >
        <div
          ref={innerRef}
          data-testid="map-inner"
          className="relative select-none"
          style={{ width: `${zoom * 100}%`, minWidth: minWidth * zoom, aspectRatio: String(aspect) }}
        >
          {imageUrl ? (
            <img src={imageUrl} alt="Planta baixa da fábrica" draggable={false}
              className="pointer-events-none absolute inset-0 size-full object-fill" />
          ) : (
            <div aria-hidden="true" className="absolute inset-0 bg-[linear-gradient(to_right,rgb(var(--c-line)/0.5)_1px,transparent_1px),linear-gradient(to_bottom,rgb(var(--c-line)/0.5)_1px,transparent_1px)] bg-[size:5%_5%]" />
          )}
          {lanes?.map((l, i) => (
            <div key={l.key} aria-hidden="true"
              className={clsx("absolute inset-x-0 border-y border-line/70", i % 2 === 0 ? "bg-surface/50" : "bg-surface-2/30")}
              style={{ top: `${(l.y - 0.5 / lanes.length) * 100}%`, height: `${100 / lanes.length}%` }} />
          ))}
          <div className={clsx("absolute inset-0 transition-opacity duration-300", dimmed && "opacity-45 grayscale-[0.4]")}>
            {children}
          </div>
        </div>
      </div>
      <div className="absolute bottom-2 right-2 flex flex-col overflow-hidden rounded-lg border border-line bg-surface shadow-card print:hidden">
        <button type="button" onClick={() => zoomAt(zoom * 1.25)} disabled={zoom >= ZOOM_MAX} aria-label="Aproximar"
          className="flex size-10 items-center justify-center text-fg-muted hover:bg-surface-2 hover:text-fg disabled:opacity-40">
          <svg viewBox="0 0 20 20" fill="currentColor" className="size-5" aria-hidden="true"><path d="M10.75 4.75a.75.75 0 0 0-1.5 0v4.5h-4.5a.75.75 0 0 0 0 1.5h4.5v4.5a.75.75 0 0 0 1.5 0v-4.5h4.5a.75.75 0 0 0 0-1.5h-4.5v-4.5Z" /></svg>
        </button>
        <button type="button" onClick={() => zoomAt(zoom / 1.25)} disabled={zoom <= ZOOM_MIN} aria-label="Afastar"
          className="flex size-10 items-center justify-center border-t border-line text-fg-muted hover:bg-surface-2 hover:text-fg disabled:opacity-40">
          <svg viewBox="0 0 20 20" fill="currentColor" className="size-5" aria-hidden="true"><path d="M4 10a.75.75 0 0 1 .75-.75h10.5a.75.75 0 0 1 0 1.5H4.75A.75.75 0 0 1 4 10Z" /></svg>
        </button>
        <button type="button" onClick={() => zoomAt(1)} disabled={zoom === 1} aria-label="Ajustar à tela"
          className="flex size-10 items-center justify-center border-t border-line text-caption font-semibold text-fg-muted hover:bg-surface-2 hover:text-fg disabled:opacity-40">
          1:1
        </button>
      </div>
    </div>
  );
});

export default MapSurface;
