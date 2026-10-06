import { Link } from "react-router-dom";
import clsx from "clsx";
import { Card, CardHeader } from "@/components/ui/Card";
import EmptyState from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { SeverityPill } from "./parts";
import { OPEN_STATES, useAlerts } from "@/hooks/usePredictive";
import { useAuthStore } from "@/store/auth";
import { can } from "@/utils/maintenance";
import { alertLink, countBySeverity, FEATURE_LABEL, rulView, sortAlerts } from "@/utils/predictive";

/** Raio-X: "O que está degradando e quando vai falhar?" (kpis.md §5 morning question, predictive.md P-12). */
export default function PredictiveCard({ lineId }: { lineId?: string }) {
  const user = useAuthStore((s) => s.user);
  const enabled = can(user, "predictive.view");
  const q = useAlerts({ state: [...OPEN_STATES], line_id: lineId }, enabled);
  if (!enabled) return null;
  const list = sortAlerts(q.data ?? []);
  const counts = countBySeverity(list);
  return (
    <Card data-testid="raiox-predictive">
      <CardHeader title="O que está degradando e quando vai falhar?" description="Alertas de condição em aberto (preditiva)."
        action={list.length > 0 ? <Link to="/maintenance/predictive" className="text-caption font-medium text-ok hover:underline">Ver todos ›</Link> : undefined} />
      {q.isPending ? <Skeleton className="h-24" /> : q.isError ? <p className="text-caption text-danger">Não foi possível carregar os alertas preditivos.</p>
        : list.length === 0 ? <EmptyState className="py-6" title="Nada degradando" description="Nenhum alerta de condição aberto nos componentes monitorados." /> : (
          <>
            <p className="mb-2 text-caption text-fg-subtle">
              {counts.critical > 0 && <span className="font-semibold text-danger">{counts.critical} crítico{counts.critical > 1 ? "s" : ""} · </span>}
              {counts.warning > 0 && <span className="font-semibold text-warn">{counts.warning} alerta{counts.warning > 1 ? "s" : ""} · </span>}
              {counts.watch} em atenção
            </p>
            <ul className="space-y-1">
              {list.slice(0, 5).map((a) => {
                const r = rulView(a.rul_condition);
                return (
                  <li key={a.id}>
                    <Link to={alertLink(a.id)} className="block rounded-lg px-2 py-2 transition-colors hover:bg-surface-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="min-w-0 truncate text-body font-medium text-fg">{a.position_name} · {FEATURE_LABEL[a.feature]}</span>
                        <SeverityPill severity={a.severity} />
                      </div>
                      <p className="flex justify-between gap-2 text-caption">
                        <span className="truncate text-fg-subtle">{a.machine_name ?? "—"}</span>
                        <span className={clsx("shrink-0", r.tone === "danger" ? "font-semibold text-danger" : r.tone === "warn" ? "font-semibold text-warn" : "text-fg-muted")}>
                          {r.headline ? `falha ${r.headline}` : "sem projeção"}
                        </span>
                      </p>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </>
        )}
    </Card>
  );
}
