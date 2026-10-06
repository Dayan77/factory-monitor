import { useMemo, useState } from "react";
import clsx from "clsx";
import { Card } from "@/components/ui/Card";
import Button from "@/components/ui/Button";
import EmptyState from "@/components/ui/EmptyState";
import QueryView from "@/components/ui/QueryView";
import Sheet from "@/components/ui/Sheet";
import { Progress } from "@/components/ui/LifeBar";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/Table";
import { Tabs, TabPanel } from "@/components/ui/Tabs";
import AlertSheet from "@/components/predictive/AlertSheet";
import MonitorSheet, { NewMonitorSheet } from "@/components/predictive/MonitorSheet";
import { AlertStatePill, ConditionPanel, SeverityPill } from "@/components/predictive/parts";
import { OPEN_STATES, useAlerts, useEvaluation, useMonitors } from "@/hooks/usePredictive";
import { usePlantTree } from "@/hooks/useAssets";
import { useAuthStore } from "@/store/auth";
import type { AlertState, Evaluation, Feature, Monitor, PredictiveAlert, PredSeverity } from "@/services/predictiveTypes";
import { can } from "@/utils/maintenance";
import {
  countBySeverity, FEATURE_LABEL, FEATURES, formatSignal, formatStat, learningProgress, num, rulView, SEVERITY_HINT,
  SEVERITY_LABEL, sortAlerts,
} from "@/utils/predictive";
import { formatDateTime, formatDec1, formatInt, formatPct } from "@/utils/format";
import { AssetFilters, Pills, useUrlFilters } from "./filters";

const KEYS = ["tab", "line", "state", "severity", "alert", "monitor", "position_id", "feature"] as const;
const STATE_FILTERS = [
  { value: "", label: "Em aberto" },
  { value: "closed", label: "Fechados" },
  { value: "all", label: "Todos" },
] as const;
const SEV_ORDER: PredSeverity[] = ["critical", "warning", "watch"];

function AlertCard({ a, onOpen }: { a: PredictiveAlert; onOpen: () => void }) {
  const rul = rulView(a.rul_condition);
  return (
    <button type="button" onClick={onOpen} data-testid="pred-alert-card" data-severity={a.severity}
      className={clsx("w-full rounded-xl border bg-surface p-4 text-left shadow-card transition-colors hover:border-line-strong",
        a.severity === "critical" && a.state !== "closed" ? "border-danger-solid/50" : a.severity === "warning" && a.state !== "closed" ? "border-warn-solid/50" : "border-line")}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-body font-semibold text-fg">{a.position_name ?? "Posição"} · {FEATURE_LABEL[a.feature]}</p>
          <p className="truncate text-caption text-fg-subtle">{[a.machine_name, a.line_name].filter(Boolean).join(" · ")}</p>
        </div>
        <div className="flex items-center gap-1.5"><SeverityPill severity={a.severity} /><AlertStatePill state={a.state} /></div>
      </div>
      {a.explanation && <p className="mt-2 line-clamp-3 text-body text-fg-muted">{a.explanation}</p>}
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-caption">
        {rul.headline ? (
          <span className={clsx("font-semibold", rul.tone === "danger" ? "text-danger" : rul.tone === "warn" ? "text-warn" : "text-fg")}>Falha {rul.headline}{a.rul_condition?.rul_days ? ` · ${rul.compact}` : ""}</span>
        ) : <span className="text-fg-subtle">Sem projeção — {rul.reason}</span>}
        <span className="text-fg-subtle">aberto {formatDateTime(a.opened_at)}{a.work_order_number ? ` · ${a.work_order_number}` : ""}</span>
      </div>
    </button>
  );
}

function AlertsTab({ line, state, severity, onSet, onOpen }: {
  line: string; state: string; severity: string; onSet: (p: Record<string, string | null>) => void; onOpen: (id: string) => void;
}) {
  const tree = usePlantTree();
  const states: AlertState[] | undefined = state === "closed" ? ["closed"] : state === "all" ? undefined : [...OPEN_STATES];
  const alerts = useAlerts({ state: states, line_id: line || undefined });
  const all = useMemo(() => sortAlerts(alerts.data ?? []), [alerts.data]);
  const counts = countBySeverity(all);
  const shown = severity ? all.filter((a) => a.severity === severity) : all;
  const sevItems = [{ value: "", label: "Todas" }, ...SEV_ORDER.map((s) => ({ value: s, label: SEVERITY_LABEL[s], count: state === "closed" ? undefined : counts[s] }))];
  return (
    <div className="space-y-4">
      <Card padded={false} className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-3">
          {tree.data && <AssetFilters tree={tree.data} plant="" line={line} machine="" showMachine={false} onChange={(p) => onSet({ line: p.line ?? null })} />}
          <Pills label="Estado" items={STATE_FILTERS} value={state as (typeof STATE_FILTERS)[number]["value"]} onChange={(v) => onSet({ state: v || null })} />
        </div>
        <Pills label="Severidade" items={sevItems} value={severity} onChange={(v) => onSet({ severity: v || null })} />
      </Card>
      <QueryView query={alerts} errorTitle="Não foi possível carregar os alertas preditivos"
        loading={<LoadingRegion label="Carregando alertas…"><div className="grid gap-3 md:grid-cols-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-36 rounded-xl" />)}</div></LoadingRegion>}
        isEmpty={() => shown.length === 0}
        empty={<Card><EmptyState title={state === "closed" ? "Nenhum alerta fechado" : "Nenhum alerta de condição em aberto"}
          description={state === "closed" ? "Os alertas encerrados aparecem aqui com a resolução." : "Nenhum componente monitorado está degradando. Os alertas aparecem quando um sinal se afasta da referência da própria posição."} /></Card>}>
        {() => (
          <div className="space-y-6">
            {SEV_ORDER.filter((s) => !severity || s === severity).map((s) => {
              const items = shown.filter((a) => a.severity === s);
              if (!items.length) return null;
              return (
                <section key={s} aria-labelledby={`sev-${s}`} className="space-y-2">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <h3 id={`sev-${s}`} className="text-heading text-fg">{SEVERITY_LABEL[s]} <span className="tabular text-fg-subtle">({items.length})</span></h3>
                    <p className="text-caption text-fg-subtle">{SEVERITY_HINT[s]}</p>
                  </div>
                  <ul className="grid gap-3 md:grid-cols-2">
                    {items.map((a) => <li key={a.id}><AlertCard a={a} onOpen={() => onOpen(a.id)} /></li>)}
                  </ul>
                </section>
              );
            })}
          </div>
        )}
      </QueryView>
    </div>
  );
}

function MonitorStateCell({ m }: { m: Monitor }) {
  const p = learningProgress(m);
  if (m.state === "disabled") return <span className="text-fg-subtle">Desligado</span>;
  if (m.state === "learning") return (
    <div className="min-w-[8rem] space-y-1">
      <span className="text-caption text-fg-muted">{m.learning?.label ?? "aprendendo"}</span>
      {p != null && <Progress value={p} size="sm" label={`Aprendizado de ${m.position_name} ${FEATURE_LABEL[m.feature]}`} />}
    </div>
  );
  return <span className="text-ok">Monitorando</span>;
}

function MonitorsTab({ line, onOpen, onCreate }: { line: string; onOpen: (id: string) => void; onCreate?: () => void }) {
  const monitors = useMonitors({ line_id: line || undefined });
  return (
    <QueryView query={monitors} errorTitle="Não foi possível carregar os monitores"
      loading={<LoadingRegion label="Carregando monitores…"><Skeleton className="h-64 rounded-xl" /></LoadingRegion>}
      isEmpty={(d) => d.length === 0}
      empty={<Card><EmptyState title="Nenhum sinal monitorado" description="Ligue o monitoramento de condição de uma posição: tempo de curso de cilindros, corrente, vibração ou temperatura de motores."
        action={onCreate ? <Button variant="primary" onClick={onCreate}>Monitorar condição</Button> : undefined} /></Card>}>
      {(list) => (
        <Card padded={false} className="p-2">
          <Table caption="Monitores de condição">
            <THead>
              <tr>
                <TH>Posição · sinal</TH>
                <TH>Estado</TH>
                <TH numeric>Último</TH>
                <TH numeric>Referência</TH>
                <TH numeric>z</TH>
                <TH numeric>EWMA</TH>
                <TH numeric>CUSUM</TH>
                <TH>Falha projetada</TH>
                <TH>Alerta</TH>
              </tr>
            </THead>
            <TBody>
              {list.map((m) => {
                const rul = rulView(m.state === "monitoring" ? m.rul_condition : null);
                return (
                  <TR key={m.id} interactive tabIndex={0} data-testid="monitor-row" onClick={() => onOpen(m.id)}
                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(m.id); } }}>
                    <TD>
                      <p className="font-medium text-fg">{m.position_name} · {FEATURE_LABEL[m.feature]}</p>
                      <p className="text-caption text-fg-subtle">{m.machine_name}</p>
                    </TD>
                    <TD><MonitorStateCell m={m} /></TD>
                    <TD numeric>{formatSignal(m.last?.x, m.unit)}</TD>
                    <TD numeric>{m.baseline?.m0 != null && m.state === "monitoring" ? `${formatSignal(m.baseline.m0, null)} ± ${formatSignal(m.baseline.sigma, null)}` : "—"}</TD>
                    <TD numeric>{formatStat(m.z, true)}</TD>
                    <TD numeric className={clsx(m.ewma != null && m.ewma_limit != null && m.ewma > m.ewma_limit && "text-warn")}>{formatStat(m.ewma)}</TD>
                    <TD numeric className={clsx(m.cusum != null && m.cusum_h != null && m.cusum > m.cusum_h && "text-warn")}>{formatStat(m.cusum)}</TD>
                    <TD>{rul.headline ? <span className={clsx(rul.tone === "danger" ? "text-danger" : rul.tone === "warn" ? "text-warn" : "text-fg")}>{rul.compact}</span> : <span className="text-caption text-fg-subtle">{m.state === "learning" ? "aprendendo" : rul.reason}</span>}</TD>
                    <TD>{m.open_alert ? <SeverityPill severity={m.open_alert.severity} /> : <span className="text-fg-subtle">—</span>}</TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        </Card>
      )}
    </QueryView>
  );
}

function Tile({ label, value, hint, testId }: { label: string; value: string; hint?: string; testId?: string }) {
  return (
    <Card className="space-y-1" data-testid={testId}>
      <p className="text-caption text-fg-subtle">{label}</p>
      <p className="text-title tabular text-fg">{value}</p>
      {hint && <p className="text-caption text-fg-subtle">{hint}</p>}
    </Card>
  );
}

const EVAL_REASON: Record<string, string> = {
  no_decided_alerts: "nenhum alerta decidido no período",
  no_failures: "nenhuma falha compatível no período",
  no_monitored_time: "sem tempo monitorado",
};

function hoursText(h: number | null): string {
  if (h == null) return "—";
  return h >= 48 ? `${formatDec1(h / 24)} dias` : `${formatDec1(h)} h`;
}

function EvaluationTab({ line }: { line: string }) {
  const ev = useEvaluation({ line_id: line || undefined });
  return (
    <QueryView query={ev} errorTitle="Não foi possível calcular o desempenho da preditiva"
      loading={<LoadingRegion label="Calculando…"><div className="grid gap-3 sm:grid-cols-3">{[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-28 rounded-xl" />)}</div></LoadingRegion>}>
      {(d: Evaluation) => {
        const reason = (k: string) => { const r = d[k]; return typeof r === "string" ? EVAL_REASON[r] ?? r : undefined; };
        const pct = (k: string) => { const v = num(d[k]); return v == null ? "—" : formatPct(v); };
        return (
          <div className="space-y-4">
            <p className="text-caption text-fg-subtle">Últimos 12 meses · verdade de campo = falha real compatível com o sinal em até {formatInt(num(d.horizon_days))} dias (predictive.md P-10).</p>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              <Tile testId="eval-precision" label="Precisão (≥ Alerta)" value={pct("precision_warning")} hint={`Meta ≥ 70 %. Em Atenção: ${pct("precision")}${reason("precision_reason") ? ` (${reason("precision_reason")})` : ""}`} />
              <Tile label="Detecção (falhas avisadas ≥ 24 h antes)" value={pct("detection_rate")} hint={num(d.failures_total) ? `${formatInt(num(d.failures_detected))} de ${formatInt(num(d.failures_total))} falhas · meta ≥ 80 %` : reason("detection_reason")} />
              <Tile label="Antecedência mediana (Alerta)" value={hoursText(num(d.lead_time_h_median_warning))} hint={`Meta ≥ 7 dias. Primeiro aviso: ${hoursText(num(d.lead_time_h_median))}`} />
              <Tile label="Falsos alarmes por posição-ano (≥ Alerta)" value={formatDec1(num(d.false_alarms_per_position_year_warning))} hint={`Meta ≤ 1. Em Atenção: ${formatDec1(num(d.false_alarms_per_position_year))}`} />
              <Tile label="Cobertura da faixa de RUL" value={pct("rul_band_coverage")} hint={`${formatInt(num(d.rul_band_checkpoints))} pontos de controle · meta ≥ 80 %`} />
              <Tile label="Alertas no período" value={formatInt(num(d.alerts))} hint={`${formatInt(num(d.true_positives))} verdadeiros · ${formatInt(num(d.false_alarms))} falsos · ${formatInt(num(d.pending))} em aberto · ${formatDec1(num(d.monitored_days))} dias monitorados`} />
            </div>
          </div>
        );
      }}
    </QueryView>
  );
}

/** Planner/manager: "O que está degradando e quando vai falhar?" (predictive.md P-12). */
export default function PredictivePage() {
  const { values: f, set } = useUrlFilters(KEYS);
  const user = useAuthStore((s) => s.user);
  const [creating, setCreating] = useState(false);
  const canEval = can(user, "predictive.evaluate");
  const canConfig = can(user, "predictive.configure");
  const tabs = [
    { value: "alerts", label: "Alertas" },
    { value: "monitors", label: "Monitores" },
    ...(canEval ? [{ value: "evaluation", label: "Desempenho" }] : []),
  ] as const;
  const tab = (tabs.some((t) => t.value === f.tab) ? f.tab : "alerts") as "alerts" | "monitors" | "evaluation";
  const feature = (FEATURES as string[]).includes(f.feature) ? (f.feature as Feature) : null;

  return (
    <div className="mx-auto max-w-[96rem] space-y-5 animate-rise-in">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-title text-fg">Preditiva</h2>
          <p className="text-caption text-fg-subtle">O que está degradando e quando vai falhar? Cada componente é comparado com a própria referência medida após a instalação.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Tabs id="pred-tab" label="Seção" items={tabs} value={tab} onChange={(v) => set({ tab: v === "alerts" ? null : v })} />
          {canConfig && <Button variant="primary" onClick={() => setCreating(true)}>Monitorar condição</Button>}
        </div>
      </div>

      <TabPanel id="pred-tab" activeValue={tab}>
        {tab === "alerts" && <AlertsTab line={f.line} state={f.state} severity={f.severity} onSet={(p) => set(p)} onOpen={(id) => set({ alert: id }, false)} />}
        {tab === "monitors" && (
          <div className="space-y-3">
            <Card padded={false} className="p-4"><div className="flex flex-wrap gap-3"><LineFilter line={f.line} onChange={(l) => set({ line: l })} /></div></Card>
            <MonitorsTab line={f.line} onOpen={(id) => set({ monitor: id }, false)} onCreate={canConfig ? () => setCreating(true) : undefined} />
          </div>
        )}
        {tab === "evaluation" && <EvaluationTab line={f.line} />}
      </TabPanel>

      <AlertSheet id={f.alert || null} onClose={() => set({ alert: null }, false)} />
      <MonitorSheet id={f.monitor || null} onClose={() => set({ monitor: null }, false)} />
      <Sheet open={!!f.position_id && !f.alert && !f.monitor} onClose={() => set({ position_id: null, feature: null }, false)} size="lg"
        title="Condição da posição" description={feature ? FEATURE_LABEL[feature] : undefined}>
        {f.position_id && <ConditionPanel positionId={f.position_id} feature={feature} />}
      </Sheet>
      <NewMonitorSheet open={creating} onClose={() => setCreating(false)} onCreated={(m) => { setCreating(false); set({ tab: "monitors", monitor: m.id }, false); }} />
    </div>
  );
}

function LineFilter({ line, onChange }: { line: string; onChange: (l: string | null) => void }) {
  const tree = usePlantTree();
  if (!tree.data) return null;
  return <AssetFilters tree={tree.data} plant="" line={line} machine="" showMachine={false} onChange={(p) => onChange(p.line ?? null)} />;
}
