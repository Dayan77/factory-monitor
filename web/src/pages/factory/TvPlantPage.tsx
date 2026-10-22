import { useEffect, useMemo, useState } from "react";
import { SUSPECT_TITLE, oeeText } from "@/utils/mapKpis";
import { Link, useParams } from "react-router-dom";
import { useRealtimeConnection } from "@/hooks/useWebSocket";
import { usePlantTree } from "@/hooks/useAssets";
import { imageVersion, useLayoutImage, usePlantMap } from "@/hooks/usePlantMap";
import { useNow } from "@/hooks/useNow";
import { useAuthStore } from "@/store/auth";
import LiveMap, { StateLegend } from "@/components/plantmap/LiveMap";
import { StateChip } from "@/components/plantmap/StateIcon";
import DemoBanner from "@/components/DemoBanner";
import { useForceDark } from "@/pages/efficiency/TvPage";
import { clockTime } from "@/utils/plantMap";
import { bySeverity, markerText } from "@/utils/visualState";
import { formatPct } from "@/utils/format";

const ROTATE_MS = 8_000;

/** Plant map on the shop-floor TV (sprint6 §1.4): no click, the highlights rotate automatically. */
export default function TvPlantPage() {
  useForceDark();
  const { plantId = "" } = useParams();
  const tenantId = useAuthStore((s) => s.user?.tenant_id);
  useRealtimeConnection(tenantId);
  const { view, query, liveness, lastUpdate, serverOffset } = usePlantMap(plantId);
  const layout = view?.kind === "published" ? view.layout : null;
  const image = useLayoutImage(layout?.id, !!layout?.has_image, imageVersion(layout));
  const now = useNow(1000) + serverOffset;
  const tree = usePlantTree();
  const tz = tree.data?.find((p) => p.id === plantId)?.timezone ?? "America/Sao_Paulo";
  const problems = useMemo(() => (view?.machines ?? []).filter((m) => m.state !== "running" && m.state !== "off_shift").sort(bySeverity), [view]);
  const [idx, setIdx] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setIdx((i) => i + 1), ROTATE_MS);
    return () => clearInterval(t);
  }, []);
  const highlight = problems.length ? problems[idx % problems.length] : null;
  const paused = liveness === "paused";

  return (
    <div className="min-h-screen bg-canvas text-fg" data-testid="tv-plant">
      <DemoBanner compact />
      <div className="p-4 sm:p-6">
        <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-display text-fg">{view?.plant_name ?? "Planta"}</h1>
            <p className="text-body text-fg-muted">{view?.shift ? view.shift.name : "fora do turno"}</p>
          </div>
          <div className="flex items-center gap-4">
            <span className="text-display tabular">{clockTime(now, tz).slice(0, 5)}</span>
            <Link to={`/factory?plant=${plantId}`} className="rounded-lg border border-line px-3 py-2 text-caption text-fg-muted hover:bg-surface-2">Sair da TV</Link>
          </div>
        </header>
        {paused && (
          <div role="alert" data-testid="live-paused" className="mb-3 rounded-xl border border-[#b45309] bg-[#fef3c7] px-4 py-3 text-heading text-[#78350f]">
            Ao vivo pausado — última atualização {lastUpdate ? clockTime(lastUpdate, tz) : "—"}
          </div>
        )}
        {query.isError ? <p className="text-heading text-danger">Não foi possível carregar o mapa.</p> : !view ? (
          <p className="text-heading text-fg-muted">Carregando…</p>
        ) : (
          <div className="grid gap-4 xl:grid-cols-[1fr_24rem]">
            <div className="min-w-0 space-y-3">
              {view.kind === "auto" && <p className="text-body text-fg-muted">{view.banner}</p>}
              <LiveMap view={view} imageUrl={image.url} now={now} dimmed={paused} selectedId={highlight?.machine_id} />
              <StateLegend />
            </div>
            <aside className="space-y-3" aria-live="polite">
              {highlight ? (
                <div className="rounded-2xl border border-line bg-surface p-5" data-testid="tv-highlight">
                  <StateChip state={highlight.state} className="text-heading" />
                  <p className="mt-3 text-display">{highlight.name}</p>
                  <p className="mt-1 text-heading text-fg-muted">{markerText(highlight, now)}</p>
                  <p className="mt-3 text-heading tabular">OEE do turno {highlight.kpis?.oee == null ? "—" : formatPct(highlight.kpis.oee)}{oeeText(highlight.kpis).suspect && <span className="ml-2 text-[#f59e0b]" title={SUSPECT_TITLE}>⚠ suspeito</span>}</p>
                  <p className="mt-2 text-caption text-fg-subtle">{(idx % Math.max(1, problems.length)) + 1} de {problems.length}</p>
                </div>
              ) : (
                <div className="rounded-2xl border border-line bg-surface p-5 text-heading text-ok">Todas as máquinas rodando.</div>
              )}
              <ul className="space-y-2">
                {view.lines.map((l) => (
                  <li key={l.line_id} className="flex items-center justify-between rounded-xl border border-line bg-surface px-4 py-3">
                    <span className="text-heading">{l.name}</span>
                    <span className="flex items-center gap-2">
                      <span className="text-heading tabular">{l.kpis?.oee == null ? "—" : formatPct(l.kpis.oee)}{oeeText(l.kpis).suspect && <span className="ml-1 text-[#f59e0b]" title={SUSPECT_TITLE}>⚠</span>}</span>
                      {l.output_state && <StateChip state={l.output_state} text="saída" />}
                    </span>
                  </li>
                ))}
              </ul>
            </aside>
          </div>
        )}
      </div>
    </div>
  );
}
