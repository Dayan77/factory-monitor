import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useLineProduction, usePlantTree, type RangeKey } from "@/hooks/useAssets";
import { useNow } from "@/hooks/useNow";
import type { LineProduction, TreeMachine } from "@/services/types";
import { Card, CardHeader } from "@/components/ui/Card";
import Stat from "@/components/ui/Stat";
import Badge from "@/components/ui/Badge";
import Breadcrumbs from "@/components/ui/Breadcrumbs";
import EmptyState from "@/components/ui/EmptyState";
import QueryView from "@/components/ui/QueryView";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { Tabs, TabPanel } from "@/components/ui/Tabs";
import MachineStatusBadge from "@/components/MachineStatusBadge";
import ProductionChart from "@/components/charts/ProductionChart";
import { formatDec1, formatInt, formatPct } from "@/utils/format";
import { formatSeenAgo } from "@/utils/time";
import { fillHourly } from "@/utils/series";
import StatusSummaryBar from "./StatusSummaryBar";
import { findLine } from "./lookup";
import DrillPeriodPicker from "@/components/drill/DrillPeriodPicker";
import DrillKpis from "@/components/drill/DrillKpis";
import ExportMenu from "@/components/reports/ExportMenu";
import { drillLink, drillPaths, parseDrill, type DrillContext } from "@/utils/drilldown";

const RANGES = [
  { value: "8h", label: "8 h" },
  { value: "24h", label: "24 h" },
  { value: "7d", label: "7 dias" },
] as const;

function MachineTile({ m, now, ctx, output, bottleneck }: { m: TreeMachine; now: number; ctx: DrillContext; output?: boolean; bottleneck?: boolean }) {
  return (
    <Link to={drillLink(drillPaths.machine(m.id), ctx)} data-testid="machine-tile" className="group block h-full rounded-xl">
      <Card interactive className="h-full">
        <div className="flex items-start justify-between gap-2">
          <p className="min-w-0 truncate text-heading text-fg">{m.name}</p>
          <MachineStatusBadge status={m.status} size="sm" />
        </div>
        {(output || bottleneck) && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {output && <Badge tone="info">Máquina de saída</Badge>}
            {bottleneck && <Badge tone="warn">Gargalo (mais parada)</Badge>}
          </div>
        )}
        <div className="mt-3 flex items-center justify-between text-caption text-fg-subtle">
          <span>{m.positions_count} {m.positions_count === 1 ? "posição" : "posições"}</span>
          <span>{formatSeenAgo(m.last_seen_at, now)}</span>
        </div>
      </Card>
    </Link>
  );
}

function ProductionBody({ data }: { data: LineProduction }) {
  if (data.reason === "no_output_machine") {
    return (
      <EmptyState
        title="Produção indisponível"
        description="Esta linha ainda não tem máquina de saída com contador de peças (tag part_counter_*)."
      />
    );
  }
  const scrapRate = data.total > 0 ? data.scrap / data.total : null;
  const points = fillHourly(data.points, data.start, data.stop, (t) => ({ t, good: 0, scrap: 0 }));
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Peças boas" value={formatInt(data.good)} tone="ok" />
        <Stat label="Refugo" value={formatInt(data.scrap)} hint={scrapRate == null ? "—" : `${formatPct(scrapRate)} do total`} tone={data.scrap > 0 ? "warn" : "neutral"} />
        <Stat label="Total" value={formatInt(data.total)} />
        <Stat
          label="Cadência média"
          value={data.cadence_per_hour == null ? "—" : formatDec1(data.cadence_per_hour)}
          unit="peças/h"
          hint={data.cadence_running_per_hour != null ? `${formatDec1(data.cadence_running_per_hour)} peças/h em operação` : undefined}
        />
      </div>
      {data.total === 0 ? (
        <EmptyState title="Sem produção no período" description="Nenhuma peça contada nesta janela de tempo." />
      ) : (
        <ProductionChart points={points} />
      )}
    </div>
  );
}

export default function LinePage() {
  const { lineId = "" } = useParams();
  const [params] = useSearchParams();
  const ctx = parseDrill(params);
  const tree = usePlantTree();
  const [range, setRange] = useState<RangeKey>("24h");
  const production = useLineProduction(lineId, range);
  const now = useNow(10_000);

  return (
    <div className="mx-auto max-w-7xl space-y-6 animate-rise-in">
      <QueryView
        query={tree}
        loading={<LoadingRegion><Skeleton className="h-16 w-72" /><Skeleton className="mt-6 h-72 rounded-xl" /></LoadingRegion>}
        errorTitle="Não foi possível carregar a linha"
      >
        {(plants) => {
          const found = findLine(plants, lineId);
          if (!found) {
            return (
              <Card>
                <EmptyState
                  title="Linha não encontrada"
                  description="Ela pode ter sido removida ou pertencer a outra empresa."
                  action={<Link to={drillLink(drillPaths.plant(), ctx)} className="text-body font-medium text-ok hover:underline">Voltar para a fábrica</Link>}
                />
              </Card>
            );
          }
          const { plant, line } = found;
          return (
            <>
              <div className="space-y-2">
                <Breadcrumbs items={[{ label: plant.name, to: drillLink(drillPaths.plant(), ctx) }, { label: line.name }]} />
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <h2 className="text-title text-fg">{line.name}</h2>
                    {line.status_summary.error > 0 && <Badge tone="danger" dot pulse>{line.status_summary.error} em falha</Badge>}
                  </div>
                  <ExportMenu report="weekly_oee" scopeType="line" scopeId={line.id} label="Exportar semana" />
                </div>
                <div className="max-w-xl"><StatusSummaryBar summary={line.status_summary} /></div>
                <DrillPeriodPicker tz={plant.timezone} />
              </div>

              <DrillKpis scope="line" id={line.id} ctx={ctx} machineIds={line.machines.map((m) => m.id)} unjustifiedLink={`/stops?line=${line.id}`} />

              <Card>
                <CardHeader
                  title="Produção e cadência"
                  description="Peças por hora (boas + refugo) da máquina de saída — a altura de cada barra é a cadência daquela hora."
                  action={<Tabs id="line-range" label="Período" items={RANGES} value={range} onChange={setRange} size="sm" />}
                />
                <TabPanel id="line-range" activeValue={range}>
                  <QueryView
                    query={production}
                    loading={<LoadingRegion label="Carregando produção…"><Skeleton className="h-24" /><Skeleton className="mt-4 h-60" /></LoadingRegion>}
                    errorTitle="Não foi possível carregar a produção"
                  >
                    {(data) => <ProductionBody data={data} />}
                  </QueryView>
                </TabPanel>
              </Card>

              <section aria-labelledby="line-machines" className="space-y-3">
                <h3 id="line-machines" className="text-heading text-fg">Máquinas</h3>
                {line.machines.length === 0 ? (
                  <Card><EmptyState title="Nenhuma máquina nesta linha" /></Card>
                ) : (
                  <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    {line.machines.map((m) => (
                      <li key={m.id}><MachineTile m={m} now={now} ctx={ctx} output={line.output_machine_id === m.id} bottleneck={line.bottleneck_machine_id === m.id} /></li>
                    ))}
                  </ul>
                )}
              </section>
            </>
          );
        }}
      </QueryView>
    </div>
  );
}
