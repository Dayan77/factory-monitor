/**
 * Predictive WO (P-9/P-12): the alert text and the condition chart that justified the work order — on the
 * technician's phone and in the planner's WO detail. Online only (the chart is not cached offline): says so.
 */
import ConditionChart from "./ConditionChart";
import { RulText, SeverityPill } from "./parts";
import { Skeleton } from "@/components/ui/Skeleton";
import { useAlert, useFeatures } from "@/hooks/usePredictive";
import { FEATURE_LABEL, alertLink } from "@/utils/predictive";
import { Link } from "react-router-dom";

export default function WoPredictiveBlock({ alertId, online = true, showLink = false }: { alertId: string; online?: boolean; showLink?: boolean }) {
  const a = useAlert(online ? alertId : null);
  const f = useFeatures({ position_id: a.data?.position_id, feature: a.data?.feature }, !!a.data);
  if (!online && !a.data) return <p className="text-caption text-fg-subtle">O gráfico e o texto do alerta aparecem quando houver conexão.</p>;
  if (a.isPending) return <Skeleton className="h-40" />;
  if (a.isError || !a.data) return <p className="text-caption text-fg-subtle">Não foi possível carregar o alerta que originou esta OS.</p>;
  const al = a.data;
  const series = f.data?.series.find((s) => s.monitor_id === al.monitor_id) ?? f.data?.series[0];
  return (
    <div className="space-y-3" data-testid="wo-predictive">
      <div className="flex flex-wrap items-center gap-2">
        <SeverityPill severity={al.severity} />
        <span className="text-caption text-fg-muted">{FEATURE_LABEL[al.feature]} · {al.position_name}</span>
        {showLink && <Link to={alertLink(al.id)} className="ml-auto text-caption font-medium text-ok hover:underline">Abrir alerta ›</Link>}
      </div>
      {al.explanation && <p className="rounded-xl border-l-4 border-warn-solid bg-surface-2/60 px-3 py-2 text-body text-fg" data-testid="wo-alert-text">{al.explanation}</p>}
      <p className="text-body"><RulText rul={al.rul_condition} /></p>
      {f.isPending ? <Skeleton className="h-44" /> : series && series.points.length > 0
        ? <ConditionChart series={series} rul={al.state === "closed" ? null : al.rul_condition} compact height={200} />
        : <p className="text-caption text-fg-subtle">Sem série do sinal para exibir.</p>}
    </div>
  );
}
