import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import clsx from "clsx";
import { usePlantTree } from "@/hooks/useAssets";
import { imageVersion, useLayoutImage, usePlantMap } from "@/hooks/usePlantMap";
import { useNow } from "@/hooks/useNow";
import { useAuthStore } from "@/store/auth";
import { Card } from "@/components/ui/Card";
import Breadcrumbs from "@/components/ui/Breadcrumbs";
import EmptyState from "@/components/ui/EmptyState";
import ErrorState from "@/components/ui/ErrorState";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { inputClass } from "@/components/ui/Field";
import LiveMap, { StateLegend } from "@/components/plantmap/LiveMap";
import MachineSummarySheet from "@/components/plantmap/MachineSummarySheet";
import { StateChip } from "@/components/plantmap/StateIcon";
import DrillPeriodPicker from "@/components/drill/DrillPeriodPicker";
import { can } from "@/utils/maintenance";
import { isDemoUser } from "@/utils/demo";
import { clockTime } from "@/utils/plantMap";
import { bySeverity, markerText } from "@/utils/visualState";
import { drillLink, drillPaths, parseDrill } from "@/utils/drilldown";
import type { VisualState } from "@/services/sprint6Types";

/** Live plant map (sprint6 §1): the floor plan with every machine's state, or the automatic layout without it. */
export default function PlantMapPage() {
  const tree = usePlantTree();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const plants = tree.data ?? [];
  const plant = plants.find((p) => p.id === params.get("plant")) ?? plants[0];
  const ctx = parseDrill(params);
  const { query, view, liveness, lastUpdate, serverOffset } = usePlantMap(plant?.id);
  const layout = view?.kind === "published" ? view.layout : null;
  const image = useLayoutImage(layout?.id, !!layout?.has_image, imageVersion(layout));
  const now = useNow(1000) + serverOffset;
  const [selected, setSelected] = useState<string | null>(null);
  const tz = plant?.timezone ?? "America/Sao_Paulo";
  const paused = liveness === "paused";
  const editable = can(user, "layout.edit") && !isDemoUser(user);

  const counts = useMemo(() => {
    const c: Partial<Record<VisualState, number>> = {};
    for (const m of view?.machines ?? []) c[m.state] = (c[m.state] ?? 0) + 1;
    return c;
  }, [view]);
  const problems = useMemo(
    () => (view?.machines ?? []).filter((m) => m.state === "failure" || m.state === "stopped" || m.state === "offline" || m.state === "maintenance").sort(bySeverity),
    [view],
  );
  const unplaced = useMemo(() => {
    if (!view || view.kind !== "published") return [];
    const ids = new Set(view.unplaced_machine_ids);
    return view.machines.filter((m) => ids.has(m.machine_id));
  }, [view]);
  const selectedMachine = view?.machines.find((m) => m.machine_id === selected) ?? null;

  if (tree.isError) return <ErrorState error={tree.error} onRetry={() => void tree.refetch()} />;

  return (
    <div className="mx-auto max-w-[96rem] space-y-4 animate-rise-in">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <Breadcrumbs items={[{ label: plant?.name ?? "Fábrica" }]} />
          <h2 className="mt-1 text-title text-fg">Mapa da planta</h2>
          <p className="text-caption text-fg-subtle">
            Estado ao vivo de cada máquina{view?.shift ? ` · ${view.shift.name}` : view ? " · fora do turno" : ""}. Clique numa máquina para o resumo.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {plants.length > 1 && (
            <select aria-label="Planta" value={plant?.id ?? ""} className={clsx(inputClass, "min-h-10 w-auto")}
              onChange={(e) => { const n = new URLSearchParams(params); n.set("plant", e.target.value); setParams(n, { replace: true }); }}>
              {plants.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          )}
          <Link to={drillLink(drillPaths.plantList(), ctx)}
            className="inline-flex min-h-10 items-center rounded-lg border border-line-strong bg-surface px-4 text-body font-medium text-fg hover:bg-surface-2">
            Ver em lista
          </Link>
          {plant && <Link to={`/tv/plants/${plant.id}`} className="inline-flex min-h-10 items-center rounded-lg border border-line-strong bg-surface px-4 text-body font-medium text-fg hover:bg-surface-2">Modo TV</Link>}
          {editable && plant && (
            <Link to={`/factory/layout/edit?plant=${plant.id}`} data-testid="edit-layout"
              className="inline-flex min-h-10 items-center rounded-lg bg-accent px-4 text-body font-semibold text-accent-fg hover:bg-accent-hover">
              Editar layout
            </Link>
          )}
        </div>
      </div>

      <DrillPeriodPicker tz={tz} label="Período do drill-down (vai junto ao abrir linha/máquina)" />

      {paused && (
        <div role="alert" data-testid="live-paused"
          className="flex flex-wrap items-center gap-2 rounded-xl border border-[#b45309] bg-[#fef3c7] px-4 py-3 text-body font-medium text-[#78350f]">
          <svg viewBox="0 0 20 20" fill="currentColor" className="size-5 shrink-0" aria-hidden="true"><path fillRule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495ZM10 6a.75.75 0 0 1 .75.75v3.5a.75.75 0 0 1-1.5 0v-3.5A.75.75 0 0 1 10 6Zm0 9a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z" clipRule="evenodd" /></svg>
          Ao vivo pausado — última atualização {lastUpdate ? clockTime(lastUpdate, tz) : "—"}. Tentando reconectar; os estados podem estar desatualizados.
        </div>
      )}

      {view?.kind === "auto" && (
        <div data-testid="auto-layout-banner" className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-line bg-surface px-4 py-3 text-body text-fg-muted">
          <span>{view.banner}</span>
          {editable && plant && <Link to={`/factory/layout/edit?plant=${plant.id}`} className="font-medium text-ok hover:underline">Enviar planta baixa</Link>}
        </div>
      )}
      {layout?.needs_review && (
        <p className="rounded-xl border border-line bg-surface px-4 py-2 text-caption text-fg-muted">A imagem da planta mudou de proporção: revise as posições no editor.</p>
      )}

      {!plant && tree.isSuccess ? (
        <Card><EmptyState title="Nenhuma planta cadastrada" description="Cadastre a planta no assistente de implantação." action={<Link to="/onboarding" className="font-medium text-ok hover:underline">Abrir assistente</Link>} /></Card>
      ) : query.isError ? (
        <ErrorState title="Não foi possível carregar o mapa" error={query.error} onRetry={() => void query.refetch()} />
      ) : !view ? (
        <LoadingRegion label="Carregando o mapa…"><Skeleton className="aspect-[16/9] w-full rounded-xl" /></LoadingRegion>
      ) : view.machines.length === 0 ? (
        <Card><EmptyState title="Planta sem máquinas" description="Cadastre linhas e máquinas para vê-las no mapa." /></Card>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[1fr_18rem]">
          <div className="min-w-0 space-y-3">
            <LiveMap view={view} imageUrl={image.url} now={now} dimmed={paused} selectedId={selected} onSelect={setSelected}
              onOpenLine={(id) => navigate(drillLink(drillPaths.line(id), ctx))} />
            <StateLegend counts={counts} />
          </div>
          <aside className="space-y-3" aria-label="Destaques">
            <Card padded={false} className="p-3">
              <h3 className="mb-2 text-caption font-semibold uppercase tracking-wide text-fg-subtle">Precisa de atenção</h3>
              {problems.length === 0 ? (
                <p className="text-caption text-fg-subtle">Nenhuma máquina em falha, parada ou sem dados.</p>
              ) : (
                <ul className="space-y-1.5" data-testid="map-problems">
                  {problems.map((m) => (
                    <li key={m.machine_id}>
                      <button type="button" onClick={() => setSelected(m.machine_id)}
                        className="flex w-full items-start gap-2 rounded-lg p-2 text-left hover:bg-surface-2">
                        <StateChip state={m.state} className="shrink-0" />
                        <span className="min-w-0">
                          <span className="block truncate text-body font-medium text-fg">{m.name}</span>
                          <span className="block truncate text-caption text-fg-subtle">{markerText(m, now)}</span>
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
            {unplaced.length > 0 && (
              <Card padded={false} className="p-3" data-testid="unplaced-tray">
                <h3 className="mb-2 text-caption font-semibold uppercase tracking-wide text-fg-subtle">Não posicionadas ({unplaced.length})</h3>
                <ul className="space-y-1.5">
                  {unplaced.map((m) => (
                    <li key={m.machine_id}>
                      <button type="button" onClick={() => setSelected(m.machine_id)} className="flex w-full items-center gap-2 rounded-lg p-1.5 text-left hover:bg-surface-2">
                        <StateChip state={m.state} />
                        <span className="truncate text-body text-fg">{m.name}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
            {view.lines.length > 0 && (
              <Card padded={false} className="p-3">
                <h3 className="mb-2 text-caption font-semibold uppercase tracking-wide text-fg-subtle">Linhas</h3>
                <ul className="space-y-1">
                  {view.lines.map((l) => (
                    <li key={l.line_id}>
                      <Link to={drillLink(drillPaths.line(l.line_id), ctx)} data-testid="map-line-link"
                        className="flex items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-body hover:bg-surface-2">
                        <span className="truncate text-fg">{l.name}</span>
                        {l.output_state ? <StateChip state={l.output_state} text="saída" /> : <span className="text-caption text-fg-subtle">sem saída</span>}
                      </Link>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </aside>
        </div>
      )}

      <MachineSummarySheet machine={selectedMachine} now={now} ctx={ctx} onClose={() => setSelected(null)} />
    </div>
  );
}
