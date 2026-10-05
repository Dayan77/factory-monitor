import { useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import clsx from "clsx";
import { Card, CardHeader } from "@/components/ui/Card";
import Badge from "@/components/ui/Badge";
import EmptyState from "@/components/ui/EmptyState";
import ErrorState from "@/components/ui/ErrorState";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import LifeBar from "@/components/ui/LifeBar";
import KpiRing from "@/components/efficiency/KpiRing";
import ApqBreakdown from "@/components/efficiency/ApqBreakdown";
import { COVERAGE_WARN } from "@/components/efficiency/DataWarnings";
import { useOverview, usePlant } from "@/hooks/useEfficiency";
import { useNow } from "@/hooks/useNow";
import type { Overview, OverviewLine, TreePlant } from "@/services/types";
import { formatDate, formatDec1, formatInt, formatPct } from "@/utils/format";
import { formatDuration, oeeTone, TONE_TEXT } from "@/utils/efficiency";
import { formatAgo, secondsSince } from "@/utils/time";

function oeeLink(scope: "line" | "machine" | "plant", id: string, extra = "") {
  return `/oee?scope=${scope}&id=${encodeURIComponent(id)}&period=today${extra}`;
}

function LineCard({ line }: { line: OverviewLine }) {
  const navigate = useNavigate();
  const scrapRate = line.good + line.scrap > 0 ? line.scrap / (line.good + line.scrap) : null;
  const lowCoverage = line.data_coverage != null && line.data_coverage < COVERAGE_WARN;
  return (
    <Card className="flex h-full flex-col gap-4" data-testid="line-card">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="truncate text-heading text-fg">
            <Link to={oeeLink("line", line.line_id)} className="rounded hover:underline">{line.name}</Link>
          </h3>
          <p className="text-caption text-fg-subtle">
            {line.current_shift_name ? `${line.current_shift_name}${line.oee_current_shift != null ? ` · OEE do turno ${formatPct(line.oee_current_shift)}` : ""}` : "Fora de turno"}
          </p>
        </div>
        {line.open_downtime_count > 0 ? (
          <Badge tone="danger" dot pulse>{line.open_downtime_count} parada{line.open_downtime_count > 1 ? "s" : ""} agora</Badge>
        ) : (
          <Badge tone="ok" dot>Sem parada aberta</Badge>
        )}
      </div>

      <button
        type="button"
        onClick={() => navigate(oeeLink("line", line.line_id))}
        className="-m-2 flex items-center gap-4 rounded-xl p-2 text-left transition-colors hover:bg-surface-2/60"
        aria-label={`Ver análise de OEE de ${line.name}`}
      >
        <KpiRing value={line.oee} label="OEE hoje" sublabel="OEE hoje" size={112} />
        <ApqBreakdown className="min-w-0 flex-1" availability={line.availability} performance={line.performance} quality={line.quality} />
      </button>

      <dl className="grid grid-cols-3 gap-2 border-t border-line pt-3 text-center">
        <div>
          <dt className="text-caption text-fg-subtle">Boas</dt>
          <dd className="text-heading tabular text-fg">{formatInt(line.good)}</dd>
        </div>
        <div>
          <dt className="text-caption text-fg-subtle">Refugo</dt>
          <dd className={clsx("text-heading tabular", line.scrap > 0 ? "text-warn" : "text-fg")}>
            {formatInt(line.scrap)}
            {scrapRate != null && line.scrap > 0 && <span className="ml-1 text-caption text-fg-subtle">({formatPct(scrapRate)})</span>}
          </dd>
        </div>
        <div>
          <dt className="text-caption text-fg-subtle">Cadência</dt>
          <dd className="text-heading tabular text-fg">
            {line.cadence == null ? "—" : formatDec1(line.cadence)}
            <span className="ml-0.5 text-caption text-fg-subtle">/h</span>
          </dd>
        </div>
      </dl>

      <div className="mt-auto space-y-2">
        <div className="flex flex-wrap gap-2">
          <Link
            to={`/stops?line=${line.line_id}`}
            className={clsx(
              "inline-flex min-h-10 items-center gap-2 rounded-lg border px-3 text-caption font-medium transition-colors",
              line.unjustified_count > 0 ? "border-warn-solid/50 bg-warn-solid/10 text-warn hover:bg-warn-solid/20" : "border-line text-fg-muted hover:bg-surface-2",
            )}
          >
            {line.unjustified_count > 0 ? `${line.unjustified_count} sem justificativa` : "Paradas justificadas"}
          </Link>
          <Link to={`/timeline?line=${line.line_id}`} className="inline-flex min-h-10 items-center rounded-lg border border-line px-3 text-caption font-medium text-fg-muted transition-colors hover:bg-surface-2">
            Linha do tempo
          </Link>
          <Link to={`/tv/lines/${line.line_id}`} className="inline-flex min-h-10 items-center rounded-lg border border-line px-3 text-caption font-medium text-fg-muted transition-colors hover:bg-surface-2">
            Modo TV
          </Link>
        </div>
        {line.worst_machine && (
          <Link to={oeeLink("machine", line.worst_machine.id)} className="flex min-h-10 items-center justify-between gap-2 rounded-lg bg-surface-2/60 px-3 py-2 text-caption transition-colors hover:bg-surface-2">
            <span className="min-w-0 truncate text-fg-muted">Pior máquina: <span className="font-medium text-fg">{line.worst_machine.name}</span></span>
            <span className={clsx("font-semibold tabular", TONE_TEXT[oeeTone(line.worst_machine.oee)])}>{formatPct(line.worst_machine.oee)}</span>
          </Link>
        )}
        {lowCoverage && (
          <p className="text-caption text-warn">Cobertura de dados {formatPct(line.data_coverage)} — números parciais.</p>
        )}
      </div>
    </Card>
  );
}

function TopLosses({ data, plantId }: { data: Overview; plantId: string | undefined }) {
  const rows = data.top_losses.filter((r) => r.duration_s > 0 && !r.planned).sort((a, b) => b.duration_s - a.duration_s);
  const max = Math.max(...rows.map((r) => r.duration_s), 1);
  return (
    <Card>
      <CardHeader title="Maiores perdas de hoje" description="Tempo perdido por motivo (paradas não planejadas, micro-paradas, velocidade, refugo)."
        action={plantId && <Link to={oeeLink("plant", plantId, "#perdas")} className="text-caption font-medium text-ok hover:underline">Ver Pareto</Link>} />
      {rows.length === 0 ? (
        <EmptyState className="py-6" title="Nenhuma perda registrada hoje" />
      ) : (
        <ol className="space-y-1">
          {rows.map((r, i) => (
            <li key={`${r.reason_code ?? r.category}-${i}`}>
              <Link to={plantId ? oeeLink("plant", plantId, "#perdas") : "/oee"} className="block rounded-lg px-2 py-2 transition-colors hover:bg-surface-2">
                <div className="flex items-baseline justify-between gap-3 text-caption">
                  <span className="min-w-0 truncate font-medium text-fg">{r.label}</span>
                  <span className="shrink-0 tabular text-fg-muted">{formatDuration(r.duration_s)}{r.pct != null && ` · ${formatPct(r.pct)}`}</span>
                </div>
                <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
                  <div className="h-full rounded-full bg-[rgb(var(--c-kpi-a))]" style={{ width: `${(r.duration_s / max) * 100}%` }} />
                </div>
              </Link>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

/** Machines in fault right now: live status from the plant tree (patched by the socket) + overview detail. */
function MachinesInFault({ tree, overview, now }: { tree: TreePlant | null; overview: Overview | undefined; now: number }) {
  const detail = useMemo(() => new Map((overview?.machines_in_error ?? []).map((m) => [m.id, m])), [overview]);
  const rows = useMemo(() => {
    const live = tree?.lines.flatMap((l) => l.machines.filter((m) => m.status === "error").map((m) => ({ m, line: l.name }))) ?? [];
    return live.map(({ m, line }) => ({ id: m.id, name: m.name, line, since: detail.get(m.id)?.since ?? null, fault: detail.get(m.id)?.fault_code ?? null }));
  }, [tree, detail]);
  return (
    <Card>
      <CardHeader title="Máquinas em falha" description="Ao vivo."
        action={rows.length > 0 ? <Badge tone="danger" dot pulse>{rows.length}</Badge> : undefined} />
      {rows.length === 0 ? (
        <EmptyState className="py-6" title="Nenhuma máquina em falha" description="Tudo rodando ou parado sem falha." />
      ) : (
        <ul className="space-y-2">
          {rows.map((r) => {
            const s = secondsSince(r.since, now);
            return (
              <li key={r.id}>
                <Link to={`/factory/machines/${r.id}`} className="flex min-h-12 items-center justify-between gap-3 rounded-lg border border-danger-solid/30 bg-danger-solid/5 px-3 py-2 transition-colors hover:bg-danger-solid/10">
                  <span className="min-w-0">
                    <span className="block truncate text-body font-medium text-fg">{r.name}</span>
                    <span className="block truncate text-caption text-fg-subtle">{r.line}{r.fault != null && ` · código ${r.fault}`}</span>
                  </span>
                  <span className="shrink-0 text-caption font-medium text-danger">{s == null ? "Falha" : formatAgo(s)}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function NearEol({ data }: { data: Overview }) {
  return (
    <Card>
      <CardHeader title="Componentes perto do fim da vida" description="Vida consumida ≥ 80%." />
      {data.components_near_eol.length === 0 ? (
        <EmptyState className="py-6" title="Nenhum componente acima de 80%" />
      ) : (
        <ul className="space-y-1">
          {data.components_near_eol.slice(0, 8).map((c) => (
            <li key={c.component_id ?? c.position_id}>
              <Link
                to={c.machine_id ? `/factory/machines/${c.machine_id}?pos=${c.position_id}` : "/factory"}
                className="block rounded-lg px-2 py-2 transition-colors hover:bg-surface-2"
              >
                <div className="flex items-baseline justify-between gap-2 text-caption">
                  <span className="min-w-0 truncate font-medium text-fg">{c.position_name}</span>
                  <span className="shrink-0 text-fg-subtle">{c.projected_end_date ? `troca ~${formatDate(c.projected_end_date)}` : ""}</span>
                </div>
                <p className="truncate text-caption text-fg-subtle">{c.machine_name ?? "—"}</p>
                <LifeBar className="mt-1" size="sm" fraction={c.life_consumed_pct} measure={c.life_measure} label={`Vida consumida — ${c.position_name}`} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export default function RaioXPage() {
  const { tree, plant } = usePlant();
  const overview = useOverview(plant?.id);
  const now = useNow(15_000);
  const data = overview.data;
  const generated = secondsSince(data?.generated_at, now);

  const lines = useMemo(
    () => (data?.lines ?? []).slice().sort((a, b) => (b.open_downtime_count - a.open_downtime_count) || ((a.oee ?? 2) - (b.oee ?? 2))),
    [data],
  );

  return (
    <div className="mx-auto max-w-7xl space-y-6 animate-rise-in">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-title text-fg">Raio-X {data?.plant_name ? `· ${data.plant_name}` : plant ? `· ${plant.name}` : ""}</h2>
          <p className="text-caption text-fg-subtle">
            Dia produtivo {data?.productive_day ? formatDate(data.productive_day) : "—"}
            {generated != null && ` · calculado ${formatAgo(generated)}`}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link to="/stops" className="inline-flex min-h-10 items-center rounded-lg bg-accent px-4 text-body font-medium text-accent-fg hover:bg-accent-hover">Justificar paradas</Link>
          <Link to={plant ? oeeLink("plant", plant.id) : "/oee"} className="inline-flex min-h-10 items-center rounded-lg border border-line-strong bg-surface px-4 text-body font-medium text-fg hover:bg-surface-2">Análise de OEE</Link>
        </div>
      </div>

      {tree.isError ? (
        <ErrorState title="Não foi possível carregar a fábrica" error={tree.error} onRetry={() => void tree.refetch()} />
      ) : tree.isSuccess && !plant ? (
        <Card><EmptyState title="Nenhuma planta cadastrada" description="Cadastre a planta, as linhas e as máquinas para ver o Raio-X." /></Card>
      ) : overview.isError && !data ? (
        <ErrorState title="Não foi possível calcular o Raio-X" error={overview.error} onRetry={() => void overview.refetch()} />
      ) : (
        <>
          <section aria-label="Linhas" >
            {!data ? (
              <LoadingRegion label="Calculando OEE das linhas…">
                <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                  {[0, 1, 2].map((i) => <Skeleton key={i} className="h-80 rounded-xl" />)}
                </div>
              </LoadingRegion>
            ) : lines.length === 0 ? (
              <Card><EmptyState title="Nenhuma linha nesta planta" /></Card>
            ) : (
              <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {lines.map((l) => <li key={l.line_id}><LineCard line={l} /></li>)}
              </ul>
            )}
          </section>

          <div className="grid gap-4 lg:grid-cols-3">
            <MachinesInFault tree={plant} overview={data} now={now} />
            {data ? <TopLosses data={data} plantId={plant?.id} /> : <Skeleton className="h-64 rounded-xl" />}
            {data ? <NearEol data={data} /> : <Skeleton className="h-64 rounded-xl" />}
          </div>
        </>
      )}
    </div>
  );
}
