/**
 * Shared predictive UI pieces: severity/state badges, RUL text, monitor summary and the per-position
 * "Condição" panel (position drawer, predictive page, technician WO).
 */
import { lazy, Suspense, useState } from "react";
import clsx from "clsx";
import Badge from "@/components/ui/Badge";
import EmptyState from "@/components/ui/EmptyState";
import ErrorState from "@/components/ui/ErrorState";
import { Progress } from "@/components/ui/LifeBar";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { Tabs } from "@/components/ui/Tabs";
// recharts only downloads when a condition chart is actually shown (the pills / RUL text are on light screens)
const ConditionChart = lazy(() => import("./ConditionChart"));
import { useFeatures, useMonitors } from "@/hooks/usePredictive";
import type { AlertState, ConditionRul, Feature, Monitor, PredSeverity } from "@/services/predictiveTypes";
import {
  ALERT_STATE_LABEL, ALERT_STATE_TONE, FEATURE_LABEL, formatSignal, formatStat, learningProgress, rulView, SEVERITY_HINT,
  SEVERITY_LABEL, SEVERITY_TONE, slopeText,
} from "@/utils/predictive";
import { formatDate } from "@/utils/format";

const SEV_ICON: Record<PredSeverity, string> = { watch: "●", warning: "▲", critical: "◆" };

/** Severity badge: icon + label + color (never color alone). */
export function SeverityPill({ severity, className }: { severity: PredSeverity; className?: string }) {
  return (
    <Badge tone={SEVERITY_TONE[severity]} className={className} title={SEVERITY_HINT[severity]}>
      <span aria-hidden="true">{SEV_ICON[severity]}</span>{SEVERITY_LABEL[severity]}
    </Badge>
  );
}

export function AlertStatePill({ state }: { state: AlertState }) {
  return <Badge tone={ALERT_STATE_TONE[state]}>{ALERT_STATE_LABEL[state]}</Badge>;
}

/** "Falha projetada em 11 dias · 09/02/2027 (faixa 06/02 – 14/02)" or the reason why there is none. */
export function RulText({ rul, className, prefix = "Falha projetada" }: { rul: ConditionRul | null | undefined; className?: string; prefix?: string }) {
  const v = rulView(rul);
  if (!v.headline) return <span className={clsx("text-fg-subtle", className)}>Sem projeção de falha — {v.reason}</span>;
  return (
    <span className={className}>
      <span className={clsx("font-semibold", v.tone === "danger" ? "text-danger" : v.tone === "warn" ? "text-warn" : "text-fg")}>{prefix} {v.headline}</span>
      {v.detail && <span className="text-fg-muted"> · {v.detail}</span>}
      {rul?.rate_source === "production_plan" && <span className="text-fg-subtle"> · pelo plano de produção</span>}
    </span>
  );
}

function Kv({ k, v, hint }: { k: string; v: React.ReactNode; hint?: string }) {
  return (
    <div className="rounded-lg bg-surface-2/60 px-3 py-2" title={hint}>
      <dt className="text-caption text-fg-subtle">{k}</dt>
      <dd className="text-body font-semibold tabular text-fg">{v}</dd>
    </div>
  );
}

/** State of a (position, signal) monitor: learning progress, or baseline + detectors + RUL. */
export function MonitorSummary({ m }: { m: Monitor }) {
  const prog = learningProgress(m);
  if (m.state === "disabled") return <p className="text-caption text-fg-subtle">Monitoramento desligado para este sinal.</p>;
  if (m.state === "learning") {
    return (
      <div className="space-y-2" data-testid="monitor-learning">
        <p className="text-body text-fg"><span className="font-semibold">{m.learning?.label ?? "aprendendo"}</span> — a referência é medida logo depois da instalação/manutenção, comparando o componente com ele mesmo.</p>
        {prog != null && <Progress value={prog} label={`Janelas da linha de base de ${FEATURE_LABEL[m.feature]}`} size="sm" />}
        <p className="text-caption text-fg-subtle">Enquanto aprende, só as regras de limite absoluto avaliam. Último valor: {formatSignal(m.last?.x, m.unit)}.</p>
      </div>
    );
  }
  const b = m.baseline;
  const slope = slopeText(m.rul_condition?.slope ?? m.last?.slope, m.feature, m.unit);
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Kv k="Último valor" v={formatSignal(m.last?.x, m.unit)} />
        <Kv k="Referência" v={b?.m0 != null ? `${formatSignal(b.m0, null)} ± ${formatSignal(b.sigma, m.unit)}` : "—"} hint={b?.established_at ? `Estabelecida em ${formatDate(b.established_at)} com ${b.n ?? "?"} janelas` : undefined} />
        <Kv k="Desvio (z)" v={formatStat(m.z, true)} hint="(x − m0) / σ, recortado em ±4" />
        <Kv k="EWMA · CUSUM" v={<>{formatStat(m.ewma)}{m.ewma_limit != null && <span className="text-caption text-fg-subtle">/{formatStat(m.ewma_limit)}</span>} · {formatStat(m.cusum)}{m.cusum_h != null && <span className="text-caption text-fg-subtle">/{formatStat(m.cusum_h)}</span>}</>} />
      </dl>
      <p className="text-body"><RulText rul={m.rul_condition} /></p>
      {slope && <p className="text-caption text-fg-subtle">Tendência: {slope}.</p>}
      {m.rul_nominal?.projected_date && (
        <p className="text-caption text-fg-subtle">
          Vida nominal: troca prevista em {formatDate(m.rul_nominal.projected_date)}{m.rul_source ? ` · vale a ${m.rul_source === "condition" ? "condição" : "vida nominal"} (a data mais cedo)` : ""}.
        </p>
      )}
    </div>
  );
}

/**
 * "Condição" of one position: its monitored signals (tabs), the state of each and the condition chart.
 * Shows explicit empty/learning states; never a z-score without a baseline.
 */
export function ConditionPanel({ positionId, feature, compact = false, chartHeight }: {
  positionId: string; feature?: Feature | null; compact?: boolean; chartHeight?: number;
}) {
  const monitors = useMonitors({ position_id: positionId });
  const [sel, setSel] = useState<Feature | null>(feature ?? null);
  const list = monitors.data ?? [];
  const current = list.find((m) => m.feature === sel) ?? list[0];
  const features = useFeatures({ position_id: positionId, feature: current?.feature }, !!current);

  if (monitors.isPending) return <LoadingRegion label="Carregando condição…"><Skeleton className="h-48" /></LoadingRegion>;
  if (monitors.isError) return <ErrorState title="Não foi possível carregar a condição" error={monitors.error} onRetry={() => void monitors.refetch()} />;
  if (!current) {
    return <EmptyState className="py-6" title="Sem monitoramento de condição" description="Nenhum sinal desta posição é acompanhado pela preditiva. O planejador liga o monitoramento em Preditiva → Monitores." />;
  }
  const series = features.data?.series.find((s) => s.feature === current.feature);
  return (
    <div className="space-y-3" data-testid="condition-panel">
      <div className="flex flex-wrap items-center justify-between gap-2">
        {list.length > 1 ? (
          <Tabs id={`cond-${positionId}`} label="Sinal monitorado" size="sm" value={current.feature}
            items={list.map((m) => ({ value: m.feature, label: FEATURE_LABEL[m.feature] }))} onChange={(v) => setSel(v)} />
        ) : <p className="text-body font-medium text-fg">{FEATURE_LABEL[current.feature]} <span className="text-fg-subtle">({current.unit})</span></p>}
        {current.open_alert && <span className="flex items-center gap-1.5"><SeverityPill severity={current.open_alert.severity} /><AlertStatePill state={current.open_alert.state} /></span>}
      </div>
      <MonitorSummary m={current} />
      {features.isPending ? <Skeleton className="h-52" /> : features.isError ? (
        <ErrorState title="Não foi possível carregar a série do sinal" error={features.error} onRetry={() => void features.refetch()} />
      ) : !series || series.points.length === 0 ? (
        <p className="text-caption text-fg-subtle">Nenhuma janela fechada ainda para este sinal.</p>
      ) : (
        <Suspense fallback={<Skeleton className="w-full rounded-lg" style={{ height: chartHeight ?? (compact ? 200 : 260) }} />}>
          <ConditionChart series={series} rul={current.state === "monitoring" ? current.rul_condition : null} compact={compact} height={chartHeight ?? (compact ? 200 : 260)} />
        </Suspense>
      )}
    </div>
  );
}
