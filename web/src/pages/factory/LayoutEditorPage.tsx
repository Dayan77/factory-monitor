import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as RPointerEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import clsx from "clsx";
import { usePlantTree } from "@/hooks/useAssets";
import { imageVersion, useLayoutImage, usePlantMap } from "@/hooks/usePlantMap";
import { useNow } from "@/hooks/useNow";
import { useAuthStore } from "@/store/auth";
import { layoutApi } from "@/services/sprint6Api";
import type { LayoutMarker, MarkerSize, PlantLayout } from "@/services/sprint6Types";
import Button from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import Breadcrumbs from "@/components/ui/Breadcrumbs";
import EmptyState from "@/components/ui/EmptyState";
import ErrorState from "@/components/ui/ErrorState";
import { inputClass } from "@/components/ui/Field";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import MapSurface from "@/components/plantmap/MapSurface";
import { MachineMarker } from "@/components/plantmap/Markers";
import { can } from "@/utils/maintenance";
import { apiErrorMessage } from "@/utils/errors";
import {
  align, duplicateMachines, historyInit, historyPush, historyRedo, historyUndo, mapAspect, markerErrors, markerKey,
  normRotation, snap, toNormalized, type History,
} from "@/utils/plantMap";

type TrayItem = { kind: "machine"; id: string; name: string; line: string } | { kind: "line_label"; id: string; name: string; line: string };

const SIZE_LABEL: Record<MarkerSize, string> = { s: "P", m: "M", l: "G" };
const SIZE_BOX: Record<MarkerSize, string> = { s: "min-w-[5.5rem] px-2 py-1", m: "w-[9.5rem] px-2.5 py-2", l: "w-[12rem] px-3 py-3" };

function sameMarkers(a: LayoutMarker[], b: LayoutMarker[]): boolean {
  return JSON.stringify(a.map(strip)) === JSON.stringify(b.map(strip));
}
function strip(m: LayoutMarker) {
  return [m.kind, m.machine_id, m.line_id, m.label, m.x, m.y, m.rotation_deg, m.size];
}

/** Layout editor (sprint6 §1.1, desktop): floor plan upload, drag machines from the tray, size/rotation, 1 % grid,
 * undo/redo, align, preview with the live state, publish. Admin and manager only. */
export default function LayoutEditorPage() {
  const tree = usePlantTree();
  const [params, setParams] = useSearchParams();
  const user = useAuthStore((s) => s.user);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const plants = tree.data ?? [];
  const plant = plants.find((p) => p.id === params.get("plant")) ?? plants[0];
  const layouts = useQuery({
    queryKey: ["layouts", plant?.id ?? ""],
    queryFn: () => layoutApi.list(plant!.id).then((r) => r.data),
    enabled: !!plant,
  });
  const draftMeta = layouts.data?.find((l) => l.status === "draft") ?? null;
  const published = layouts.data?.find((l) => l.status === "published") ?? null;
  const draft = useQuery({
    queryKey: ["layout", draftMeta?.id ?? ""],
    queryFn: () => layoutApi.get(draftMeta!.id).then((r) => r.data),
    enabled: !!draftMeta,
  });

  const createDraft = useMutation({
    mutationFn: async () => {
      try {
        return (await layoutApi.createDraft(plant!.id)).data;
      } catch (err) {
        if (isAxiosError(err) && err.response?.status === 409) return null;      // a draft already exists
        throw err;
      }
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["layouts", plant?.id ?? ""] }),
  });

  if (!can(user, "layout.edit")) {
    return (
      <Card className="mx-auto max-w-3xl"><EmptyState title="Sem permissão para editar o layout" description="Só administradores e gestores editam o mapa da planta." /></Card>
    );
  }
  if (tree.isError) return <ErrorState error={tree.error} onRetry={() => void tree.refetch()} />;

  return (
    <div className="mx-auto max-w-[96rem] space-y-4 animate-rise-in">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <Breadcrumbs items={[{ label: plant?.name ?? "Fábrica", to: `/factory${plant ? `?plant=${plant.id}` : ""}` }, { label: "Editor de layout" }]} />
          <h2 className="mt-1 text-title text-fg">Editor do mapa da planta</h2>
          <p className="text-caption text-fg-subtle">
            Envie a planta baixa, arraste as máquinas da bandeja e publique. Publicado: {published ? `versão ${published.version}` : "nenhum (a tela usa o layout automático)"}.
          </p>
        </div>
        {plants.length > 1 && (
          <select aria-label="Planta" value={plant?.id ?? ""} className={clsx(inputClass, "w-auto")}
            onChange={(e) => setParams({ plant: e.target.value }, { replace: true })}>
            {plants.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        )}
      </div>

      {tree.isPending || layouts.isPending ? (
        <LoadingRegion><Skeleton className="aspect-[16/9] rounded-xl" /></LoadingRegion>
      ) : layouts.isError ? (
        <ErrorState error={layouts.error} onRetry={() => void layouts.refetch()} />
      ) : !plant ? (
        <Card><EmptyState title="Nenhuma planta cadastrada" /></Card>
      ) : !draftMeta ? (
        <Card>
          <EmptyState
            title="Nenhum rascunho em edição"
            description={published
              ? `Editar cria o rascunho da versão ${published.version + 1} a partir da publicada (imagem e posições). A publicada continua no ar até você publicar o rascunho.`
              : "Crie o primeiro layout: envie a planta baixa e posicione as máquinas."}
            action={<Button variant="primary" loading={createDraft.isPending} onClick={() => createDraft.mutate()} data-testid="create-draft">
              {published ? `Criar rascunho v${published.version + 1}` : "Criar layout"}
            </Button>}
          />
          {createDraft.isError && <p role="alert" className="text-center text-caption text-danger">{apiErrorMessage(createDraft.error, "Não foi possível criar o rascunho.")}</p>}
        </Card>
      ) : draft.isPending ? (
        <LoadingRegion><Skeleton className="aspect-[16/9] rounded-xl" /></LoadingRegion>
      ) : draft.isError ? (
        <ErrorState error={draft.error} onRetry={() => void draft.refetch()} />
      ) : (
        <Editor key={draft.data.id} layout={draft.data} plantId={plant.id}
          lines={plant.lines.map((l) => ({ id: l.id, name: l.name, machines: l.machines.map((m) => ({ id: m.id, name: m.name })) }))}
          onPublished={() => navigate(`/factory?plant=${plant.id}`)} />
      )}
    </div>
  );
}

interface EditorProps {
  layout: PlantLayout;
  plantId: string;
  lines: { id: string; name: string; machines: { id: string; name: string }[] }[];
  onPublished: () => void;
}

function Editor({ layout, plantId, lines, onPublished }: EditorProps) {
  const qc = useQueryClient();
  const [hist, setHist] = useState<History<LayoutMarker[]>>(() => historyInit(layout.markers ?? []));
  const [saved, setSaved] = useState<LayoutMarker[]>(layout.markers ?? []);
  const markers = hist.present;
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [grid, setGrid] = useState(true);
  const [preview, setPreview] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [msg, setMsg] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [areaText, setAreaText] = useState("");
  const inner = useRef<HTMLDivElement>(null);
  const drag = useRef<{ keys: string[]; start: { x: number; y: number }; orig: Map<string, { x: number; y: number }>; moved: boolean; base: LayoutMarker[] } | null>(null);
  const [ghost, setGhost] = useState<{ item: TrayItem; x: number; y: number } | null>(null);
  const image = useLayoutImage(layout.id, layout.has_image, imageVersion(layout));
  const live = usePlantMap(preview ? plantId : undefined);
  const now = useNow(1000);
  const dirty = !sameMarkers(markers, saved);

  const machineName = useMemo(() => {
    const m = new Map<string, { name: string; line: string }>();
    for (const l of lines) for (const x of l.machines) m.set(x.id, { name: x.name, line: l.name });
    return m;
  }, [lines]);
  const lineName = useMemo(() => new Map(lines.map((l) => [l.id, l.name])), [lines]);
  const tray: TrayItem[] = useMemo(() => {
    const placedM = new Set(markers.filter((m) => m.kind === "machine").map((m) => m.machine_id));
    const placedL = new Set(markers.filter((m) => m.kind === "line_label").map((m) => m.line_id));
    const out: TrayItem[] = [];
    for (const l of lines) {
      if (!placedL.has(l.id)) out.push({ kind: "line_label", id: l.id, name: `Rótulo: ${l.name}`, line: l.name });
      for (const m of l.machines) if (!placedM.has(m.id)) out.push({ kind: "machine", id: m.id, name: m.name, line: l.name });
    }
    return out;
  }, [markers, lines]);

  const commit = useCallback((next: LayoutMarker[]) => setHist((h) => historyPush(h, next)), []);
  const errors = markerErrors(markers);

  const save = useMutation({
    mutationFn: () => layoutApi.putMarkers(layout.id, markers).then((r) => r.data),
    onSuccess: (lay) => {
      setSaved(lay.markers ?? markers);
      setMsg({ tone: "ok", text: "Rascunho salvo." });
      void qc.invalidateQueries({ queryKey: ["layouts", plantId] });
    },
    onError: (e) => setMsg({ tone: "error", text: apiErrorMessage(e, "Não foi possível salvar os marcadores.") }),
  });
  const upload = useMutation({
    mutationFn: (f: File) => layoutApi.uploadImage(layout.id, f).then((r) => r.data),
    onSuccess: (lay) => {
      setMsg({ tone: "ok", text: lay.needs_review ? "Imagem trocada com proporção diferente: revise as posições antes de publicar." : "Planta baixa enviada." });
      qc.setQueryData(["layout", layout.id], lay);
      void qc.invalidateQueries({ queryKey: ["layouts", plantId] });
    },
    onError: (e) => setMsg({ tone: "error", text: apiErrorMessage(e, "Imagem recusada.") }),
  });
  const publish = useMutation({
    mutationFn: async () => {
      if (dirty) await layoutApi.putMarkers(layout.id, markers);
      return (await layoutApi.publish(layout.id)).data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["layouts", plantId] });
      void qc.invalidateQueries({ queryKey: ["plant-map"] });
      onPublished();
    },
    onError: (e) => setMsg({ tone: "error", text: apiErrorMessage(e, "Não foi possível publicar.") }),
  });

  // keyboard: undo/redo, delete, arrows (1 % / 5 % with Shift)
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, select")) return;
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); setHist((h) => (e.shiftKey ? historyRedo(h) : historyUndo(h))); return; }
      if (mod && e.key.toLowerCase() === "y") { e.preventDefault(); setHist(historyRedo); return; }
      if (selected.size === 0) return;
      if (e.key === "Delete" || e.key === "Backspace") {
        e.preventDefault();
        commit(markers.filter((m, i) => !selected.has(markerKey(m, i))));
        setSelected(new Set());
        return;
      }
      const step = e.shiftKey ? 0.05 : 0.01;
      const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
      if (d) {
        e.preventDefault();
        commit(markers.map((m, i) => (selected.has(markerKey(m, i)) ? { ...m, x: snap(m.x + d[0], 0.01, grid), y: snap(m.y + d[1], 0.01, grid) } : m)));
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [markers, selected, grid, commit]);

  // leaving with unsaved changes
  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);

  function rect() {
    return inner.current?.getBoundingClientRect() ?? { left: 0, top: 0, width: 1, height: 1 };
  }

  function addFromTray(item: TrayItem, x = 0.5, y = 0.5) {
    const m: LayoutMarker = item.kind === "machine"
      ? { kind: "machine", machine_id: item.id, line_id: null, label: null, x: snap(x, 0.01, grid), y: snap(y, 0.01, grid), rotation_deg: 0, size: "m" }
      : { kind: "line_label", machine_id: null, line_id: item.id, label: null, x: snap(x, 0.01, grid), y: snap(y, 0.01, grid), rotation_deg: 0, size: "m" };
    const next = [...markers, m];
    if (duplicateMachines(next).length) return;
    commit(next);
    setSelected(new Set([markerKey(m, next.length - 1)]));
  }

  function onMarkerDown(e: RPointerEvent<HTMLElement>, key: string) {
    if (e.button !== 0) return;
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    let sel = selected;
    if (e.shiftKey) {
      sel = new Set(selected);
      if (sel.has(key)) sel.delete(key); else sel.add(key);
    } else if (!selected.has(key)) sel = new Set([key]);
    setSelected(sel);
    const p = toNormalized(e.clientX, e.clientY, rect());
    const orig = new Map<string, { x: number; y: number }>();
    markers.forEach((m, i) => { const k = markerKey(m, i); if (sel.has(k)) orig.set(k, { x: m.x, y: m.y }); });
    drag.current = { keys: [...sel], start: p, orig, moved: false, base: markers };
  }

  function onMarkerMove(e: RPointerEvent<HTMLElement>) {
    const d = drag.current;
    if (!d) return;
    const p = toNormalized(e.clientX, e.clientY, rect());
    const dx = p.x - d.start.x;
    const dy = p.y - d.start.y;
    if (!d.moved && Math.abs(dx) + Math.abs(dy) < 0.002) return;
    d.moved = true;
    // live move without history entries; the drop commits one entry
    setHist((h) => ({
      ...h,
      present: d.base.map((m, i) => {
        const o = d.orig.get(markerKey(m, i));
        return o ? { ...m, x: snap(o.x + dx, 0.01, grid), y: snap(o.y + dy, 0.01, grid) } : m;
      }),
    }));
  }

  function onMarkerUp() {
    const d = drag.current;
    drag.current = null;
    if (!d || !d.moved) return;
    setHist((h) => ({ past: [...h.past, d.base].slice(-100), present: h.present, future: [] }));
  }

  // tray → map drag (pointer events: mouse, pen and touch)
  function onTrayDown(e: RPointerEvent<HTMLElement>, item: TrayItem) {
    if (e.button !== 0) return;
    e.preventDefault();
    setGhost({ item, x: e.clientX, y: e.clientY });
    const move = (ev: PointerEvent) => setGhost((g) => (g ? { ...g, x: ev.clientX, y: ev.clientY } : g));
    const up = (ev: PointerEvent) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      setGhost(null);
      const r = rect();
      if (ev.clientX >= r.left && ev.clientX <= r.left + r.width && ev.clientY >= r.top && ev.clientY <= r.top + r.height) {
        const p = toNormalized(ev.clientX, ev.clientY, r);
        addFromTray(item, p.x, p.y);
      }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  const selIdx = markers.findIndex((m, i) => selected.has(markerKey(m, i)));
  const sel = selected.size === 1 && selIdx >= 0 ? markers[selIdx] : null;
  function patchSelected(p: Partial<LayoutMarker>) {
    commit(markers.map((m, i) => (selected.has(markerKey(m, i)) ? { ...m, ...p } : m)));
  }

  const liveById = useMemo(() => new Map((live.view?.machines ?? []).map((m) => [m.machine_id, m])), [live.view]);
  const aspect = mapAspect(layout.image_width_px, layout.image_height_px);

  return (
    <div className="grid gap-4 lg:grid-cols-[16rem_1fr_17rem]">
      {/* tray */}
      <Card padded={false} className="order-2 p-3 lg:order-1">
        <h3 className="mb-1 text-caption font-semibold uppercase tracking-wide text-fg-subtle">Não posicionadas ({tray.length})</h3>
        <p className="mb-2 text-caption text-fg-subtle">Arraste para o mapa (ou selecione e toque em “Colocar”).</p>
        <p className="mb-2 text-caption text-fg-subtle" id="editor-keys">Teclado: Enter na lista coloca no mapa; no mapa, Enter seleciona (Shift+Enter soma), setas movem (Shift = passo maior), Delete remove, Ctrl+Z desfaz.</p>
        {tray.length === 0 ? <p className="text-caption text-ok">Todas as máquinas estão no mapa.</p> : (
          <ul className="max-h-[60vh] space-y-1.5 overflow-y-auto" data-testid="editor-tray">
            {tray.map((t) => (
              <li key={`${t.kind}:${t.id}`} className="flex items-center gap-1">
                <span role="button" tabIndex={0} onPointerDown={(e) => onTrayDown(e, t)} data-testid="tray-item" data-id={t.id}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); addFromTray(t); } }}
                  aria-label={`Arrastar ${t.name} para o mapa`}
                  className="flex min-h-10 min-w-0 flex-1 cursor-grab touch-none items-center gap-2 rounded-lg border border-line bg-surface-2/40 px-2 text-body hover:border-accent active:cursor-grabbing">
                  <svg viewBox="0 0 20 20" fill="currentColor" className="size-4 shrink-0 text-fg-subtle" aria-hidden="true"><path d="M7 4a1 1 0 1 1-2 0 1 1 0 0 1 2 0Zm0 6a1 1 0 1 1-2 0 1 1 0 0 1 2 0Zm-1 7a1 1 0 1 0 0-2 1 1 0 0 0 0 2Zm9-13a1 1 0 1 1-2 0 1 1 0 0 1 2 0Zm-1 7a1 1 0 1 0 0-2 1 1 0 0 0 0 2Zm1 5a1 1 0 1 1-2 0 1 1 0 0 1 2 0Z" /></svg>
                  <span className="min-w-0"><span className="block truncate text-fg">{t.name}</span><span className="block truncate text-caption text-fg-subtle">{t.line}</span></span>
                </span>
                <button type="button" onClick={() => addFromTray(t)} className="min-h-10 rounded-lg px-2 text-caption font-medium text-ok hover:bg-surface-2">Colocar</button>
              </li>
            ))}
          </ul>
        )}
        <div className="mt-4 border-t border-line pt-3">
          <label htmlFor="area-label" className="text-caption font-medium text-fg-muted">Rótulo de área</label>
          <div className="mt-1 flex gap-1">
            <input id="area-label" value={areaText} onChange={(e) => setAreaText(e.target.value)} maxLength={120} placeholder="Ex.: Almoxarifado" className={clsx(inputClass, "min-h-10")} />
            <Button size="md" disabled={!areaText.trim()} onClick={() => {
              const m: LayoutMarker = { kind: "area_label", machine_id: null, line_id: null, label: areaText.trim(), x: 0.5, y: 0.5, rotation_deg: 0, size: "m" };
              commit([...markers, m]);
              setAreaText("");
            }}>Adicionar</Button>
          </div>
        </div>
      </Card>

      {/* map */}
      <div className="order-1 min-w-0 space-y-2 lg:order-2">
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-surface p-2 shadow-card">
          <label className={clsx("inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-lg border border-line-strong px-3 text-body font-medium text-fg hover:bg-surface-2", upload.isPending && "opacity-60")}>
            <input type="file" accept="image/png,image/jpeg,image/svg+xml" className="sr-only" data-testid="upload-image"
              disabled={upload.isPending} onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ""; }} />
            {upload.isPending ? "Enviando…" : layout.has_image ? "Trocar planta baixa" : "Enviar planta baixa"}
          </label>
          <span className="h-6 w-px bg-line" aria-hidden="true" />
          <Button size="sm" variant="ghost" onClick={() => setHist(historyUndo)} disabled={hist.past.length === 0} aria-label="Desfazer (Ctrl+Z)" title="Desfazer (Ctrl+Z)">↶ Desfazer</Button>
          <Button size="sm" variant="ghost" onClick={() => setHist(historyRedo)} disabled={hist.future.length === 0} aria-label="Refazer (Ctrl+Shift+Z)" title="Refazer (Ctrl+Shift+Z)">↷ Refazer</Button>
          <Button size="sm" variant="ghost" disabled={selected.size < 2} onClick={() => commit(align(markers, selected, "y"))} title="Alinhar horizontalmente (mesmo Y)">Alinhar —</Button>
          <Button size="sm" variant="ghost" disabled={selected.size < 2} onClick={() => commit(align(markers, selected, "x"))} title="Alinhar verticalmente (mesmo X)">Alinhar |</Button>
          <label className="inline-flex min-h-8 items-center gap-1.5 text-caption text-fg-muted">
            <input type="checkbox" checked={grid} onChange={(e) => setGrid(e.target.checked)} className="size-4 accent-[rgb(var(--c-accent))]" />
            Grade 1 %
          </label>
          <label className="inline-flex min-h-8 items-center gap-1.5 text-caption text-fg-muted">
            <input type="checkbox" checked={preview} onChange={(e) => setPreview(e.target.checked)} data-testid="preview-toggle" className="size-4 accent-[rgb(var(--c-accent))]" />
            Pré-visualizar ao vivo
          </label>
        </div>

        {!layout.has_image && (
          <p className="rounded-lg border border-line bg-surface px-3 py-2 text-caption text-fg-muted">Sem planta baixa: posicione sobre a grade e envie a imagem (PNG, JPEG ou SVG, até 10 MB) antes de publicar.</p>
        )}
        {layout.needs_review && <p className="rounded-lg border border-[#b45309] bg-[#fef3c7] px-3 py-2 text-caption text-[#78350f]">A proporção da imagem mudou: revise as posições.</p>}

        <MapSurface ref={inner} aspect={aspect} imageUrl={image.url} zoom={zoom} onZoom={setZoom} label="Mapa em edição" minWidth={640}>
          <div className="absolute inset-0" onPointerDown={() => setSelected(new Set())} aria-hidden="true" />
          {grid && <div aria-hidden="true" className="pointer-events-none absolute inset-0 bg-[linear-gradient(to_right,rgb(var(--c-accent)/0.10)_1px,transparent_1px),linear-gradient(to_bottom,rgb(var(--c-accent)/0.10)_1px,transparent_1px)] bg-[size:1%_1%]" />}
          {markers.map((m, i) => {
            const key = markerKey(m, i);
            const isSel = selected.has(key);
            const style = { left: `${m.x * 100}%`, top: `${m.y * 100}%`, transform: `translate(-50%, -50%) rotate(${m.rotation_deg}deg)` };
            const lv = m.machine_id ? liveById.get(m.machine_id) : undefined;
            const name = m.kind === "machine" ? machineName.get(m.machine_id ?? "")?.name ?? "Máquina" : m.kind === "line_label" ? lineName.get(m.line_id ?? "") ?? "Linha" : m.label ?? "";
            return (
              <div key={key} data-marker data-testid="editor-marker" data-key={key}
                role="button" tabIndex={0} aria-pressed={isSel}
                aria-label={`${name}${m.kind === "line_label" ? " (rótulo de linha)" : m.kind === "area_label" ? " (rótulo de área)" : ""} — posição ${Math.round(m.x * 100)} %, ${Math.round(m.y * 100)} %`}
                onPointerDown={(e) => onMarkerDown(e, key)} onPointerMove={onMarkerMove} onPointerUp={onMarkerUp}
                // keyboard: Enter/Space selects (Shift adds to the selection); then arrows move, Delete removes
                onKeyDown={(e) => {
                  if (e.key !== "Enter" && e.key !== " ") return;
                  e.preventDefault();
                  if (e.shiftKey) {
                    const next = new Set(selected);
                    if (next.has(key)) next.delete(key); else next.add(key);
                    setSelected(next);
                  } else setSelected(new Set([key]));
                }}
                className={clsx("absolute z-10 cursor-move touch-none rounded-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent", isSel && "z-20")} style={style}>
                {preview && lv ? (
                  <div className={clsx("pointer-events-none rounded-lg", isSel && "ring-4 ring-accent")}>
                    <MachineMarker marker={{ ...m, x: 0.5, y: 0.5, rotation_deg: 0 }} machine={lv} now={now} />
                  </div>
                ) : (
                  <div className={clsx(
                    "rounded-lg border-2 bg-surface/95 text-caption shadow-card",
                    m.kind === "machine" ? SIZE_BOX[m.size] : "rounded-full px-3 py-1",
                    m.kind === "area_label" && "border-dashed uppercase",
                    isSel ? "border-accent ring-4 ring-accent/40" : "border-line-strong",
                  )}>
                    <span className="block truncate font-semibold text-fg">{name}</span>
                    {m.kind === "machine" && m.size !== "s" && <span className="block truncate text-fg-subtle">{machineName.get(m.machine_id ?? "")?.line}</span>}
                  </div>
                )}
              </div>
            );
          })}
        </MapSurface>
        {ghost && (
          <div aria-hidden="true" className="pointer-events-none fixed z-50 -translate-x-1/2 -translate-y-1/2 rounded-lg border-2 border-accent bg-surface px-3 py-2 text-caption font-semibold text-fg shadow-pop"
            style={{ left: ghost.x, top: ghost.y }}>{ghost.item.name}</div>
        )}
      </div>

      {/* inspector */}
      <div className="order-3 space-y-3">
        <Card padded={false} className="p-3">
          <h3 className="mb-2 text-caption font-semibold uppercase tracking-wide text-fg-subtle">Seleção</h3>
          {selected.size === 0 ? <p className="text-caption text-fg-subtle">Clique num marcador (Shift para vários). Setas movem 1 % (Shift: 5 %); Delete remove.</p>
            : selected.size > 1 ? <p className="text-caption text-fg-muted">{selected.size} marcadores selecionados — use alinhar ou as setas.</p>
            : sel && (
              <div className="space-y-3" data-testid="inspector">
                <p className="text-body font-medium text-fg">
                  {sel.kind === "machine" ? machineName.get(sel.machine_id ?? "")?.name : sel.kind === "line_label" ? `Rótulo: ${lineName.get(sel.line_id ?? "")}` : sel.label}
                </p>
                <p className="tabular text-caption text-fg-subtle">x {(sel.x * 100).toFixed(0)} % · y {(sel.y * 100).toFixed(0)} %</p>
                {sel.kind === "machine" && (
                  <div role="group" aria-label="Tamanho" className="flex gap-1">
                    {(["s", "m", "l"] as MarkerSize[]).map((s) => (
                      <button key={s} type="button" aria-pressed={sel.size === s} onClick={() => patchSelected({ size: s })}
                        className={clsx("min-h-10 flex-1 rounded-lg border text-body font-semibold", sel.size === s ? "border-accent bg-accent/15 text-ok" : "border-line text-fg-muted hover:bg-surface-2")}>
                        {SIZE_LABEL[s]}
                      </button>
                    ))}
                  </div>
                )}
                <div>
                  <label htmlFor="rot" className="text-caption font-medium text-fg-muted">Rotação ({sel.rotation_deg}°)</label>
                  <div className="mt-1 flex items-center gap-1">
                    <Button size="sm" onClick={() => patchSelected({ rotation_deg: normRotation(sel.rotation_deg - 15) })} aria-label="Girar −15°">−15°</Button>
                    <input id="rot" type="range" min={0} max={359} step={1} value={sel.rotation_deg}
                      onChange={(e) => setHist((h) => ({ ...h, present: h.present.map((m, i) => (selected.has(markerKey(m, i)) ? { ...m, rotation_deg: normRotation(Number(e.target.value)) } : m)) }))}
                      onPointerDown={() => setHist((h) => historyPush(h, [...h.present]))}
                      className="min-w-0 flex-1 accent-[rgb(var(--c-accent))]" />
                    <Button size="sm" onClick={() => patchSelected({ rotation_deg: normRotation(sel.rotation_deg + 15) })} aria-label="Girar +15°">+15°</Button>
                  </div>
                </div>
                <Button variant="danger-ghost" size="sm" onClick={() => { commit(markers.filter((_, i) => i !== selIdx)); setSelected(new Set()); }}>
                  Remover do mapa
                </Button>
              </div>
            )}
        </Card>
        <Card padded={false} className="space-y-2 p-3">
          <p className="text-caption text-fg-muted">Rascunho v{layout.version} · {markers.length} marcadores{dirty ? " · alterações não salvas" : ""}</p>
          {errors.length > 0 && <ul className="text-caption text-danger">{errors.map((e) => <li key={e}>{e}</li>)}</ul>}
          <Button className="w-full" onClick={() => save.mutate()} loading={save.isPending} disabled={!dirty || errors.length > 0} data-testid="save-draft">Salvar rascunho</Button>
          <Button variant="primary" className="w-full" onClick={() => publish.mutate()} loading={publish.isPending} disabled={!layout.has_image || errors.length > 0} data-testid="publish-layout"
            title={layout.has_image ? undefined : "Envie a planta baixa antes de publicar"}>
            Publicar
          </Button>
          {!layout.has_image && <p className="text-caption text-fg-subtle">Publicar exige a planta baixa.</p>}
          {msg && <p role={msg.tone === "error" ? "alert" : "status"} className={clsx("text-caption", msg.tone === "error" ? "text-danger" : "text-ok")}>{msg.text}</p>}
          <Link to={`/factory?plant=${plantId}`} className="block text-center text-caption font-medium text-ok hover:underline">Voltar ao mapa</Link>
        </Card>
      </div>
    </div>
  );
}
