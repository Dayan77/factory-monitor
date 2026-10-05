import { useMemo } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipProps } from "recharts";
import { Card, CardHeader } from "@/components/ui/Card";
import Badge from "@/components/ui/Badge";
import EmptyState from "@/components/ui/EmptyState";
import QueryView from "@/components/ui/QueryView";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/Table";
import { Tabs, TabPanel } from "@/components/ui/Tabs";
import ChartFrame from "@/components/charts/ChartFrame";
import { useReliability } from "@/hooks/useMaintenance";
import { componentTypeLabel } from "@/utils/assets";
import { usePlant } from "@/hooks/useEfficiency";
import type { ReliabilityGroupBy, ReliabilityRow } from "@/services/maintenanceTypes";
import { formatDate, formatDec1, formatInt } from "@/utils/format";
import { formatDuration } from "@/utils/efficiency";
import { zonedToUtc } from "@/utils/period";
import { addDays, isoDay, reliabilityReason } from "@/utils/maintenance";
import { useUrlFilters } from "./filters";

const GROUPS = [
  { value: "position", label: "Posição" },
  { value: "component_type", label: "Tipo de componente" },
  { value: "manufacturer", label: "Fabricante" },
  { value: "manufacturer_model", label: "Fabricante / modelo (MTTF)" },
] as const;

/** 12 months ending today 00:00 (plant time zone) — S4-8. */
export function reliabilityWindow(today: string, tz: string) {
  const [y, m, d] = today.split("-").map(Number);
  const stop = zonedToUtc(y, m, d, 0, 0, tz);
  const start = zonedToUtc(y - 1, m, d, 0, 0, tz);
  return { start: new Date(start).toISOString(), stop: new Date(stop).toISOString() };
}

/** Human label of a row (server labels carry raw type codes). */
export function rowLabel(r: ReliabilityRow, group: ReliabilityGroupBy): string {
  if (group === "component_type") return componentTypeLabel(r.component_type ?? r.label);
  if (group === "manufacturer" && r.manufacturer) return `${r.manufacturer} · ${componentTypeLabel(r.component_type)}`;
  if (group === "position" && r.machine_name) return `${r.label} · ${r.machine_name}`;
  return r.label;
}

/** A value or "—" with the reason (never a fake 0). */
function Val({ v, reason, children }: { v: number | null | undefined; reason: string | null; children: (v: number) => string }) {
  if (v == null) {
    return (
      <span className="inline-flex flex-col items-end" title={reliabilityReason(reason)}>
        <span className="text-fg-subtle">—</span>
        <span className="text-[0.6875rem] text-fg-subtle">{reliabilityReason(reason)}</span>
      </span>
    );
  }
  return <>{children(v)}</>;
}

const hours = (v: number) => `${formatInt(v)} h`;
const mttr = (v: number) => formatDuration(v * 3600);

function MtbfTooltip({ active, payload }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  const r = payload[0].payload as ReliabilityRow;
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-caption shadow-pop">
      <p className="mb-1 font-medium text-fg">{r.label}</p>
      <p className="text-fg-muted">MTBF <span className="font-semibold tabular text-fg">{r.mtbf_h == null ? "—" : hours(r.mtbf_h)}</span></p>
      <p className="text-fg-muted">n = {r.n} falha{r.n === 1 ? "" : "s"}{r.mttr_h != null ? ` · MTTR ${mttr(r.mttr_h)}` : ""}</p>
    </div>
  );
}

/** Bad actors: lowest MTBF first (single series — no legend, the title names it). */
function MtbfChart({ rows, group }: { rows: ReliabilityRow[]; group: ReliabilityGroupBy }) {
  const data = rows.filter((r) => r.mtbf_h != null).sort((a, b) => (a.mtbf_h as number) - (b.mtbf_h as number)).slice(0, 12)
    .map((r) => ({ ...r, label: rowLabel(r, group) }));
  if (data.length === 0) return <EmptyState className="py-6" title="Sem MTBF calculável na janela" description="Nenhum grupo teve falhas críticas ou degradadas." />;
  const height = Math.max(160, data.length * 36 + 40);
  return (
    <ChartFrame label={`MTBF por grupo, menor primeiro: ${data.map((r) => `${r.label} ${hours(r.mtbf_h as number)}`).join("; ")}`} height={height}
      table={{ headers: ["Grupo", "MTBF (h)", "n"], rows: data.map((r) => [r.label, formatDec1(r.mtbf_h), r.n]) }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 8 }} barCategoryGap={8}>
          <CartesianGrid horizontal={false} stroke="rgb(var(--c-grid))" strokeDasharray="3 3" />
          <XAxis type="number" tickFormatter={(v: number) => formatInt(v)} tick={{ fill: "rgb(var(--c-fg-subtle))", fontSize: 11 }} axisLine={false} tickLine={false} />
          <YAxis type="category" dataKey="label" width={230} tick={{ fill: "rgb(var(--c-fg-muted))", fontSize: 12 }} axisLine={false} tickLine={false} />
          <Tooltip content={<MtbfTooltip />} cursor={{ fill: "rgb(var(--c-fg) / 0.06)" }} />
          <Bar dataKey="mtbf_h" name="MTBF" fill="rgb(var(--c-kpi-a))" radius={[0, 4, 4, 0]} maxBarSize={22} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

function ReliabilityTable({ rows, group }: { rows: ReliabilityRow[]; group: ReliabilityGroupBy }) {
  const mttf = group === "manufacturer_model";
  const sorted = rows.slice().sort((a, b) => (a.mtbf_h ?? Infinity) - (b.mtbf_h ?? Infinity) || a.label.localeCompare(b.label, "pt-BR"));
  return (
    <Table caption="Indicadores de confiabilidade">
      <THead>
        <tr>
          <TH>{GROUPS.find((g) => g.value === group)?.label}</TH>
          <TH numeric>n</TH>
          {mttf ? (
            <><TH numeric>MTTF</TH><TH numeric>Falhas · suspensões</TH><TH numeric>Em serviço</TH></>
          ) : (
            <><TH numeric>MTBF</TH><TH numeric>MCBF (ciclos)</TH><TH numeric>MTTR</TH><TH numeric>Restabelecidas</TH><TH numeric>Incipientes</TH></>
          )}
        </tr>
      </THead>
      <TBody>
        {sorted.map((r) => {
          const failures = r.failures_removed ?? r.n;
          const mttfValue = r.mttf ?? r.mttf_h;
          const unit = r.mttf_measure === "cycles" ? "ciclos" : r.mttf_measure === "starts" ? "partidas" : "h";
          return (
            <TR key={r.key} data-testid="reliability-row">
              <TD className="font-medium">{rowLabel(r, group)}</TD>
              <TD numeric>{r.n}</TD>
              {mttf ? (
                <>
                  <TD numeric><Val v={mttfValue} reason={r.mttf_reason ?? r.null_reason}>{(v) => `${formatInt(v)} ${unit}`}</Val></TD>
                  <TD numeric>
                    <span>n = {failures} falha{failures === 1 ? "" : "s"}, {r.suspensions} suspens{r.suspensions === 1 ? "ão" : "ões"}</span>
                    {(r.small_sample ?? (failures > 0 && failures < 3)) && <Badge tone="warn" className="ml-2">amostra pequena</Badge>}
                  </TD>
                  <TD numeric>{r.in_service ?? "—"}</TD>
                </>
              ) : (
                <>
                  <TD numeric><Val v={r.mtbf_h} reason={r.null_reason}>{hours}</Val></TD>
                  <TD numeric><Val v={r.mcbf} reason={r.mcbf == null && r.mtbf_h != null ? "no_cycles" : r.null_reason}>{formatInt}</Val></TD>
                  <TD numeric><Val v={r.mttr_h} reason={r.mttr_reason ?? (r.mttr_h == null && r.n > 0 ? "no_restorations" : r.null_reason)}>{mttr}</Val></TD>
                  <TD numeric>{r.restored}</TD>
                  <TD numeric>{r.incipient}</TD>
                </>
              )}
            </TR>
          );
        })}
      </TBody>
    </Table>
  );
}

/** Manager/planner: MTBF / MCBF / MTTR / MTTF by position, type, manufacturer and model (kpis.md §2, S4-8…S4-11). */
export default function ReliabilityPage() {
  const { values: f, set } = useUrlFilters(["group"] as const);
  const { tz } = usePlant();
  const today = isoDay(Date.now(), tz);
  const group = (GROUPS.some((g) => g.value === f.group) ? f.group : "position") as ReliabilityGroupBy;
  const win = useMemo(() => reliabilityWindow(today, tz), [today, tz]);
  const q = useReliability({ group_by: group, ...win });

  return (
    <div className="mx-auto max-w-7xl space-y-5 animate-rise-in">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-title text-fg">Confiabilidade</h2>
          <p className="text-caption text-fg-subtle">
            Últimos 12 meses ({formatDate(addDays(today, -365))} – {formatDate(addDays(today, -1))}). Só falhas críticas e degradadas entram em MTBF/MCBF/MTTR; MTTR só com restabelecimento.
          </p>
        </div>
        <Tabs id="rel-group" label="Agrupar por" items={GROUPS} value={group} onChange={(v) => set({ group: v === "position" ? null : v })} size="sm" />
      </div>
      <TabPanel id="rel-group" activeValue={group} className="space-y-5">
        <QueryView query={q} errorTitle="Não foi possível calcular a confiabilidade"
          loading={<LoadingRegion label="Calculando…"><Skeleton className="h-64 rounded-xl" /></LoadingRegion>}
          isEmpty={(d) => d.length === 0} empty={<Card><EmptyState title="Nada a mostrar na janela" description="Sem ativos ou componentes com histórico neste agrupamento." /></Card>}>
          {(rows) => (
            <>
              {group !== "manufacturer_model" && (
                <Card>
                  <CardHeader title="MTBF — maus atores primeiro" description="Tempo médio entre falhas (horas de operação). Menor = pior." />
                  <MtbfChart rows={rows} group={group} />
                </Card>
              )}
              <Card padded={false} className="p-2">
                <ReliabilityTable rows={rows} group={group} />
              </Card>
              {group === "manufacturer_model" && (
                <p className="text-caption text-fg-subtle">MTTF: média da idade na remoção dos componentes removidos por falha (todo o histórico). Trocas preventivas são suspensões e ficam fora da média.</p>
              )}
            </>
          )}
        </QueryView>
      </TabPanel>
    </div>
  );
}
