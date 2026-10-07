import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/services/api";
import type { DowntimeEvent, OeeResult, OeeScope, ParetoRow } from "@/services/types";
import { Card, CardHeader } from "@/components/ui/Card";
import Sheet from "@/components/ui/Sheet";
import { Skeleton } from "@/components/ui/Skeleton";
import ErrorState from "@/components/ui/ErrorState";
import { useReasonCodes } from "@/hooks/useEfficiency";
import { formatDateTime, formatDec1, formatInt, formatPct } from "@/utils/format";
import { formatDuration, reasonLabel } from "@/utils/efficiency";
import { drillApiParams, DRILL_PERIOD_LABELS, kpiReason, type DrillContext } from "@/utils/drilldown";

export const drillKeys = {
  oee: (scope: OeeScope, id: string, ctx: DrillContext) => ["efficiency", "drill-oee", scope, id, ctx] as const,
  pareto: (scope: OeeScope, id: string, ctx: DrillContext) => ["efficiency", "drill-pareto", scope, id, ctx] as const,
};

export function useDrillOee(scope: OeeScope, id: string | undefined, ctx: DrillContext) {
  return useQuery({
    queryKey: drillKeys.oee(scope, id ?? "", ctx),
    queryFn: () => api.get<OeeResult & { period?: { productive_day?: string; shift_name?: string } }>("/api/oee", { params: { scope, id, ...drillApiParams(ctx) } }).then((r) => r.data),
    enabled: !!id,
    refetchInterval: 60_000,
  });
}

export function useDrillPareto(scope: OeeScope, id: string | undefined, ctx: DrillContext) {
  return useQuery({
    queryKey: drillKeys.pareto(scope, id ?? "", ctx),
    queryFn: () => api.get<ParetoRow[]>("/api/losses/pareto", { params: { scope, id, ...drillApiParams(ctx) } }).then((r) => r.data),
    enabled: !!id,
    refetchInterval: 60_000,
  });
}

function Kpi({ label, value, testId }: { label: string; value: number | null | undefined; testId?: string }) {
  return (
    <div className="min-w-0 rounded-xl border border-line bg-surface p-3" data-testid={testId}>
      <p className="text-caption text-fg-subtle">{label}</p>
      <p className="text-title tabular text-fg">{value == null ? "—" : formatPct(value)}</p>
    </div>
  );
}

interface Props {
  scope: OeeScope;
  id: string | undefined;
  ctx: DrillContext;
  /** Machine ids of the scope (to filter the stop list of a Pareto bar). */
  machineIds: string[];
  /** Link to the unjustified stops of this scope. */
  unjustifiedLink: string;
}

/**
 * KPIs of one drill-down level for the shared period (§2.1): OEE/A/P/Q (never a mean of the children, kpis.md §1.6),
 * parts, Pareto top-5 with each bar clickable to the stops behind it (§2.2 rule 3).
 */
export default function DrillKpis({ scope, id, ctx, machineIds, unjustifiedLink }: Props) {
  const oee = useDrillOee(scope, id, ctx);
  const pareto = useDrillPareto(scope, id, ctx);
  const [params, setParams] = useSearchParams();
  const openReason = params.get("stops");
  const top = useMemo(
    () => (pareto.data ?? []).filter((r) => !r.planned && r.duration_s > 0).sort((a, b) => b.duration_s - a.duration_s).slice(0, 5),
    [pareto.data],
  );
  const maxS = top[0]?.duration_s ?? 1;
  const unjustified = (pareto.data ?? []).find((r) => r.reason_code === "UNK-01" || r.category === "UNK");
  const why = kpiReason(oee.data?.oee == null ? oee.data?.reason : null);
  const d = oee.data;

  function openStops(code: string | null) {
    const n = new URLSearchParams(params);
    if (code) n.set("stops", code); else n.delete("stops");
    setParams(n, { replace: false });
  }

  return (
    <section aria-label="Indicadores do período" className="space-y-3">
      {oee.isError ? (
        <ErrorState title="Não foi possível calcular o OEE" error={oee.error} onRetry={() => void oee.refetch()} />
      ) : !d ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">{Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-20 rounded-xl" />)}</div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <Kpi label="OEE" value={d.oee} testId="drill-oee" />
            <Kpi label="Disponibilidade" value={d.availability} />
            <Kpi label="Desempenho" value={d.performance} />
            <Kpi label="Qualidade" value={d.quality} />
            <div className="rounded-xl border border-line bg-surface p-3">
              <p className="text-caption text-fg-subtle">Peças boas</p>
              <p className="text-title tabular text-fg">{formatInt(d.good)}</p>
            </div>
            <div className="rounded-xl border border-line bg-surface p-3">
              <p className="text-caption text-fg-subtle">Refugo</p>
              <p className="text-title tabular text-fg">{formatInt(d.scrap)}</p>
            </div>
          </div>
          {why && (
            <p className="rounded-lg border border-line bg-surface px-3 py-2 text-caption text-fg-muted" data-testid="kpi-reason">
              OEE indefinido: {why.text}.{" "}
              {why.fix && <Link to={why.fix.to} className="font-medium text-ok hover:underline">{why.fix.label}</Link>}
            </p>
          )}
        </>
      )}

      <Card>
        <CardHeader title="Maiores perdas (Pareto)" as="h3"
          description={`${DRILL_PERIOD_LABELS[ctx.period]}${d?.period?.productive_day ? ` · dia produtivo ${d.period.productive_day.split("-").reverse().join("/")}` : ""} · toque numa barra para ver as paradas`}
          action={unjustified && unjustified.duration_s > 0 ? (
            <Link to={unjustifiedLink} className="text-caption font-medium text-ok hover:underline" data-testid="unjustified-link">
              {formatDuration(unjustified.duration_s)} sem motivo
            </Link>
          ) : undefined} />
        {pareto.isPending ? <Skeleton className="h-40" /> : pareto.isError ? (
          <ErrorState title="Não foi possível carregar o Pareto" error={pareto.error} onRetry={() => void pareto.refetch()} />
        ) : top.length === 0 ? (
          <p className="py-6 text-center text-caption text-fg-subtle">Sem perdas registradas no período.</p>
        ) : (
          <ol className="space-y-2" data-testid="drill-pareto">
            {top.map((r) => {
              const code = r.reason_code ?? r.category;
              const pseudo = ["micro_stops", "speed_loss", "quality_loss"].includes(code);
              return (
                <li key={code}>
                  <button type="button" disabled={pseudo} onClick={() => openStops(code)} data-testid="pareto-bar" data-minutes={r.duration_s / 60}
                    className="group w-full rounded-lg p-1.5 text-left hover:bg-surface-2 disabled:cursor-default disabled:hover:bg-transparent">
                    <span className="flex items-baseline justify-between gap-3 text-body">
                      <span className="truncate text-fg">{r.label}</span>
                      <span className="shrink-0 tabular text-fg-muted">{formatDec1(r.duration_s / 60)} min · {r.events}×</span>
                    </span>
                    <span className="mt-1 block h-2 overflow-hidden rounded-full bg-surface-2">
                      <span className="block h-full rounded-full bg-warn-solid" style={{ width: `${Math.max(2, (r.duration_s / maxS) * 100)}%` }} />
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        )}
      </Card>

      <StopsSheet reason={openReason} onClose={() => openStops(null)} start={d?.period_start} stop={d?.period_end}
        scope={scope} id={id} machineIds={machineIds} />
    </section>
  );
}

function StopsSheet({ reason, onClose, start, stop, scope, id, machineIds }: {
  reason: string | null; onClose: () => void; start?: string; stop?: string; scope: OeeScope; id?: string; machineIds: string[];
}) {
  const reasons = useReasonCodes();
  const q = useQuery({
    queryKey: ["downtime", { drill: true, scope, id, start, stop }],
    queryFn: () => api.get<DowntimeEvent[]>("/api/downtime/", {
      params: { ...(scope === "line" ? { line_id: id } : scope === "machine" ? { machine_id: id } : {}), start, stop, limit: 500 },
    }).then((r) => r.data),
    enabled: !!reason && !!start && !!stop,
  });
  const set = new Set(machineIds);
  const rows = (q.data ?? []).filter((e) => {
    const code = e.effective_reason_code ?? e.reason_code ?? "UNK-01";
    const match = reason && (code === reason || code.startsWith(`${reason}-`) || (reason === "UNK-01" && (!e.reason_code || e.reason_code === "UNK-01")));
    return match && (scope !== "plant" || set.has(e.machine_id));
  });
  const total = rows.reduce((s, e) => s + (e.duration_s ?? 0), 0);
  return (
    <Sheet open={!!reason} onClose={onClose} title={`Paradas — ${reasonLabel(reason, reasons.data)}`}
      description={start && stop ? `${formatDateTime(start)} – ${formatDateTime(stop)} · ${rows.length} paradas · ${formatDuration(total)}` : undefined}>
      {q.isPending ? <Skeleton className="h-40" /> : q.isError ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : rows.length === 0 ? (
        <p className="text-caption text-fg-subtle">Nenhuma parada com este motivo no período.</p>
      ) : (
        <ul className="divide-y divide-line rounded-lg border border-line" data-testid="drill-stops">
          {rows.map((e) => (
            <li key={e.id} className="flex items-center justify-between gap-3 px-3 py-2">
              <div className="min-w-0">
                <p className="truncate text-body text-fg">{e.machine_name ?? "Máquina"}</p>
                <p className="text-caption text-fg-subtle">{formatDateTime(e.start)}</p>
              </div>
              <span className="shrink-0 tabular text-caption text-fg-muted">{e.end ? formatDuration(e.duration_s) : "em aberto"}</span>
            </li>
          ))}
        </ul>
      )}
    </Sheet>
  );
}
