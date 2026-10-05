import { useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import clsx from "clsx";
import { useMachineUsage, usePlantTree, useUsageHistory, type RangeKey, type UsageScope } from "@/hooks/useAssets";
import { useNow } from "@/hooks/useNow";
import { useRealtimeStore } from "@/store/realtime";
import type { MachineUsage, MachineUsagePosition, UsageHistory, UsageMeasure } from "@/services/types";
import { Card, CardHeader } from "@/components/ui/Card";
import Stat from "@/components/ui/Stat";
import Badge from "@/components/ui/Badge";
import Breadcrumbs from "@/components/ui/Breadcrumbs";
import EmptyState from "@/components/ui/EmptyState";
import QueryView from "@/components/ui/QueryView";
import LifeBar from "@/components/ui/LifeBar";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/Table";
import { Tabs, TabPanel } from "@/components/ui/Tabs";
import { inputClass } from "@/components/ui/Field";
import MachineStatusBadge from "@/components/MachineStatusBadge";
import UsageChart from "@/components/charts/UsageChart";
import { componentTypeLabel, MEASURE_LABEL } from "@/utils/assets";
import { formatDec1, formatInt, formatPct } from "@/utils/format";
import { formatSeenAgo } from "@/utils/time";
import { fillHourly } from "@/utils/series";
import PositionDrawer from "./PositionDrawer";
import { findMachine } from "./lookup";

const MACHINE_MEASURES = [
  { value: "good", label: "Boas" },
  { value: "scrap", label: "Refugo" },
] as const;
const POSITION_MEASURES = [
  { value: "cycles", label: "Ciclos" },
  { value: "hours", label: "Horas" },
  { value: "starts", label: "Partidas" },
] as const;
const RANGES = [
  { value: "24h", label: "24 h" },
  { value: "7d", label: "7 dias" },
] as const;

/** Most worn first; positions without nominal life at the end. */
function byLife(a: MachineUsagePosition, b: MachineUsagePosition) {
  return (b.life_consumed_pct ?? -1) - (a.life_consumed_pct ?? -1) || a.name.localeCompare(b.name, "pt-BR");
}

function useTick(key: string | null): number | undefined {
  return useRealtimeStore((s) => (key ? s.usageTick[key] : undefined));
}

function LiveNumber({ value, tickKey, className }: { value: string; tickKey: string; className?: string }) {
  const tick = useTick(tickKey);
  return (
    <span key={tick} className={clsx("rounded px-1 tabular", tick !== undefined && "animate-flash", className)}>
      {value}
    </span>
  );
}

function PositionsTable({ usage, onOpen }: { usage: MachineUsage; onOpen: (id: string) => void }) {
  const rows = [...usage.positions].sort(byLife);
  if (rows.length === 0) {
    return <EmptyState title="Nenhuma posição cadastrada" description="Cadastre as posições (cilindros, motores, eixos…) desta máquina para acompanhar o uso." />;
  }
  return (
    <>
      {/* ≥ md: table */}
      <div className="hidden md:block">
        <Table caption="Posições e componentes da máquina">
          <THead>
            <tr>
              <TH>Posição</TH>
              <TH numeric>Ciclos</TH>
              <TH numeric>Horas</TH>
              <TH numeric>Partidas</TH>
              <TH className="w-52">Vida consumida</TH>
            </tr>
          </THead>
          <TBody>
            {rows.map((p) => (
              <TR
                key={p.position_id}
                interactive
                tabIndex={0}
                data-testid="position-row"
                aria-label={`Abrir posição ${p.name}`}
                onClick={() => onOpen(p.position_id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onOpen(p.position_id);
                  }
                }}
              >
                <TD>
                  <p className="font-medium text-fg">{p.name}</p>
                  <p className="text-caption text-fg-subtle">
                    {componentTypeLabel(p.component_type)}
                    {!p.component_id && " · sem componente instalado"}
                  </p>
                </TD>
                <TD numeric>
                  <LiveNumber value={formatInt(p.cycles_total)} tickKey={p.position_id} />
                  {p.cycles_estimated && <span className="ml-1 text-caption text-fg-subtle" title="Estimado: peças × ciclos por peça">≈</span>}
                </TD>
                <TD numeric>{formatDec1(p.hours_total)}</TD>
                <TD numeric>{formatInt(p.starts_total)}</TD>
                <TD>
                  {p.component_id ? (
                    <LifeBar fraction={p.life_consumed_pct} measure={p.life_measure} label={`Vida consumida — ${p.name}`} />
                  ) : (
                    <span className="text-caption text-fg-subtle">Sem componente</span>
                  )}
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </div>
      {/* < md: cards */}
      <ul className="space-y-2 md:hidden">
        {rows.map((p) => (
          <li key={p.position_id}>
            <button
              type="button"
              onClick={() => onOpen(p.position_id)}
              className="w-full rounded-lg border border-line bg-surface p-3 text-left transition-colors hover:bg-surface-2/60"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate font-medium text-fg">{p.name}</p>
                  <p className="text-caption text-fg-subtle">{componentTypeLabel(p.component_type)}</p>
                </div>
                <svg viewBox="0 0 20 20" fill="currentColor" className="mt-0.5 size-4 shrink-0 text-fg-subtle" aria-hidden="true">
                  <path fillRule="evenodd" d="M7.21 14.77a.75.75 0 0 1 .02-1.06L11.17 10 7.23 6.29a.75.75 0 1 1 1.04-1.08l4.5 4.25a.75.75 0 0 1 0 1.08l-4.5 4.25a.75.75 0 0 1-1.06-.02z" clipRule="evenodd" />
                </svg>
              </div>
              <dl className="mt-2 grid grid-cols-3 gap-2 text-caption">
                <div><dt className="text-fg-subtle">Ciclos</dt><dd className="font-semibold tabular text-fg">{formatInt(p.cycles_total)}</dd></div>
                <div><dt className="text-fg-subtle">Horas</dt><dd className="font-semibold tabular text-fg">{formatDec1(p.hours_total)}</dd></div>
                <div><dt className="text-fg-subtle">Partidas</dt><dd className="font-semibold tabular text-fg">{formatInt(p.starts_total)}</dd></div>
              </dl>
              <div className="mt-2">
                {p.component_id ? (
                  <LifeBar fraction={p.life_consumed_pct} measure={p.life_measure} label={`Vida consumida — ${p.name}`} size="sm" />
                ) : (
                  <span className="text-caption text-fg-subtle">Sem componente instalado</span>
                )}
              </div>
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}

function HistoryBody({ data, measure }: { data: UsageHistory; measure: UsageMeasure }) {
  const points = fillHourly(data.points, data.start, data.stop, (t) => ({ t, value: 0 }));
  if (data.points.length === 0) {
    return <EmptyState title="Sem uso registrado no período" description={`Nenhum valor de ${MEASURE_LABEL[measure].toLowerCase()} nesta janela.`} />;
  }
  return <UsageChart points={points} measure={measure} />;
}

function UsageHistoryCard({ machineId, usage }: { machineId: string; usage: MachineUsage | undefined }) {
  const [scopeKey, setScopeKey] = useState<string>("machine");
  const [machineMeasure, setMachineMeasure] = useState<"good" | "scrap">("good");
  const [positionMeasure, setPositionMeasure] = useState<"cycles" | "hours" | "starts">("cycles");
  const [range, setRange] = useState<RangeKey>("24h");
  const isMachine = scopeKey === "machine";
  const scope: UsageScope = isMachine ? { machine_id: machineId } : { position_id: scopeKey };
  const measure: UsageMeasure = isMachine ? machineMeasure : positionMeasure;
  const history = useUsageHistory(scope, measure, range);

  return (
    <Card>
      <CardHeader
        title="Histórico de uso"
        description="Soma por hora."
        action={<Tabs id="usage-range" label="Período" items={RANGES} value={range} onChange={setRange} size="sm" />}
      />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <label className="sr-only" htmlFor="usage-scope">Escopo</label>
        <select
          id="usage-scope"
          value={scopeKey}
          onChange={(e) => setScopeKey(e.target.value)}
          className={clsx(inputClass.replace("w-full ", ""), "w-full sm:w-auto sm:min-w-[16rem]")}
        >
          <option value="machine">Produção da máquina</option>
          {usage?.positions.map((p) => (
            <option key={p.position_id} value={p.position_id}>Posição: {p.name}</option>
          ))}
        </select>
        {isMachine ? (
          <Tabs id="usage-measure" label="Medida" items={MACHINE_MEASURES} value={machineMeasure} onChange={setMachineMeasure} size="sm" />
        ) : (
          <Tabs id="usage-measure" label="Medida" items={POSITION_MEASURES} value={positionMeasure} onChange={setPositionMeasure} size="sm" />
        )}
      </div>
      <TabPanel id="usage-measure" activeValue={measure}>
        <QueryView
          query={history}
          loading={<LoadingRegion label="Carregando histórico…"><Skeleton className="h-60" /></LoadingRegion>}
          errorTitle="Não foi possível carregar o histórico"
        >
          {(data) => <HistoryBody data={data} measure={measure} />}
        </QueryView>
      </TabPanel>
    </Card>
  );
}

export default function MachinePage() {
  const { machineId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const openPosition = params.get("pos");
  const tree = usePlantTree();
  const usage = useMachineUsage(machineId);
  const now = useNow(5_000);
  const productionTick = useTick(machineId);

  const found = useMemo(() => (tree.data ? findMachine(tree.data, machineId) : null), [tree.data, machineId]);

  function setPosition(id: string | null) {
    const next = new URLSearchParams(params);
    if (id) next.set("pos", id);
    else next.delete("pos");
    setParams(next, { replace: false });
  }

  if (tree.isSuccess && !found) {
    return (
      <div className="mx-auto max-w-7xl">
        <Card>
          <EmptyState
            title="Máquina não encontrada"
            description="Ela pode ter sido removida ou pertencer a outra empresa."
            action={<Link to="/factory" className="text-body font-medium text-ok hover:underline">Voltar para a fábrica</Link>}
          />
        </Card>
      </div>
    );
  }

  const prod = usage.data?.production;
  const worn = usage.data?.positions.filter((p) => p.component_id && (p.life_consumed_pct ?? 0) > 0.9).length ?? 0;
  const scrapRate = prod && prod.total > 0 ? prod.scrap / prod.total : null;

  return (
    <div className="mx-auto max-w-7xl space-y-6 animate-rise-in">
      <div className="space-y-2">
        {found ? (
          <Breadcrumbs
            items={[
              { label: "Fábrica", to: "/factory" },
              { label: found.plant.name, to: "/factory" },
              { label: found.line.name, to: `/factory/lines/${found.line.id}` },
              { label: found.machine.name },
            ]}
          />
        ) : (
          <Skeleton className="h-4 w-64" />
        )}
        <div className="flex flex-wrap items-center gap-3">
          {found ? <h2 className="text-title text-fg">{found.machine.name}</h2> : <Skeleton className="h-7 w-48" />}
          {found && <MachineStatusBadge status={found.machine.status} />}
          {found && <span className="text-caption text-fg-subtle">{formatSeenAgo(found.machine.last_seen_at, now)}</span>}
        </div>
      </div>

      <section aria-label="Produção de hoje" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {usage.isPending ? (
          Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-24 rounded-xl" />)
        ) : prod ? (
          <>
            <Stat label="Boas hoje" value={formatInt(prod.good)} tone="ok" flashKey={productionTick} />
            <Stat label="Refugo hoje" value={formatInt(prod.scrap)} tone={prod.scrap > 0 ? "warn" : "neutral"} hint={scrapRate == null ? undefined : `${formatPct(scrapRate)} do total`} />
            <Stat label="Total hoje" value={formatInt(prod.total)} hint={prod.timezone ? `Dia produtivo (${prod.timezone})` : undefined} />
            <Stat
              label="Componentes acima de 90%"
              value={worn}
              unit={`/ ${usage.data?.positions.length ?? 0}`}
              hint={worn > 0 ? "Planejar a troca" : "Nenhum perto do fim da vida"}
              tone={worn > 0 ? "danger" : "neutral"}
            />
          </>
        ) : null}
      </section>

      <Card>
        <CardHeader
          title="Posições e componentes"
          description="Uso acumulado da posição e vida consumida do componente instalado. Mais desgastados primeiro."
          action={
            <div className="hidden items-center gap-2 text-caption text-fg-subtle sm:flex" aria-hidden="true">
              <Badge tone="ok" dot>&lt; 70%</Badge>
              <Badge tone="warn" dot>70–90%</Badge>
              <Badge tone="danger" dot>&gt; 90%</Badge>
            </div>
          }
        />
        <QueryView
          query={usage}
          loading={<LoadingRegion label="Carregando posições…"><div className="space-y-2">{Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-12" />)}</div></LoadingRegion>}
          errorTitle="Não foi possível carregar o uso da máquina"
        >
          {(data) => <PositionsTable usage={data} onOpen={setPosition} />}
        </QueryView>
      </Card>

      <UsageHistoryCard machineId={machineId} usage={usage.data} />

      <PositionDrawer positionId={openPosition} onClose={() => setPosition(null)} />
    </div>
  );
}
