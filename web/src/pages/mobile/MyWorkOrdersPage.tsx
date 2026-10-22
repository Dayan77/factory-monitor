import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import clsx from "clsx";
import Badge from "@/components/ui/Badge";
import EmptyState from "@/components/ui/EmptyState";
import ErrorState from "@/components/ui/ErrorState";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { OverdueBadge, WoStateBadge, WoTypeBadge } from "@/components/maintenance/badges";
import { transitionErrorMessage } from "@/components/maintenance/transitionError";
import { useEmergencyCalls, useMyWorkOrders, useQueueState, useTechAction } from "@/hooks/useTechnician";
import { useWoNames } from "@/hooks/useMaintenance";
import { useAuthStore } from "@/store/auth";
import { applyPending } from "@/services/offline";
import type { WorkOrder } from "@/services/maintenanceTypes";
import { formatDate, formatDateTime } from "@/utils/format";
import { can, dayOf, hoursLabel, isoDay, isWoOverdue, remainingHours, sortForTechnician, technicianRank } from "@/utils/maintenance";

const SECTION = ["Emergência", "Vencidas", "Em execução", "Hoje", "Próximas"];

function WoBigCard({ wo, today, pending }: { wo: WorkOrder; today: string; pending: number }) {
  const names = useWoNames();
  return (
    <Link to={`/m/work-orders/${wo.id}`} data-testid="my-wo" data-wo={wo.number}
      className={clsx("block rounded-2xl border-2 bg-surface p-4 shadow-card active:bg-surface-2",
        wo.urgency === "emergency" && wo.state !== "completed" ? "border-danger-solid/60" : isWoOverdue(wo, today) ? "border-danger-solid/40" : "border-line")}>
      <div className="flex items-start justify-between gap-2">
        <span className="text-heading text-fg">{wo.number}</span>
        <WoStateBadge state={wo.state} />
      </div>
      <p className="mt-1 text-body font-semibold text-fg">{wo.title}</p>
      <p className="text-body text-fg-muted">{names.machine(wo)}{wo.position_name ? ` · ${wo.position_name}` : ""}</p>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <WoTypeBadge type={wo.type} urgency={wo.urgency} />
        {isWoOverdue(wo, today) && <OverdueBadge />}
        {wo.stops_machine && <Badge tone="warn">Para a máquina</Badge>}
        {pending > 0 && <Badge tone="info" dot pulse>{pending} pendente{pending > 1 ? "s" : ""} de envio</Badge>}
      </div>
      <p className="mt-2 text-caption text-fg-subtle">
        {wo.scheduled_for ? `Programada ${formatDate(wo.scheduled_for)}` : "Sem data"} · {hoursLabel(wo.estimated_hours)} estimadas · {hoursLabel(remainingHours(wo))} restantes
      </p>
    </Link>
  );
}

function EmergencyCard({ wo }: { wo: WorkOrder }) {
  const names = useWoNames();
  const action = useTechAction();
  const navigate = useNavigate();
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="rounded-2xl border-2 border-danger-solid bg-surface p-4 shadow-card" data-testid="emergency-call">
      <div className="flex items-center gap-2"><Badge tone="danger" dot pulse>Chamado emergencial</Badge><span className="text-caption text-fg-subtle">{formatDateTime(wo.requested_at ?? wo.created_at)}</span></div>
      <p className="mt-2 text-heading text-fg">{wo.title}</p>
      <p className="text-body text-fg-muted">{names.machine(wo)}{wo.position_name ? ` · ${wo.position_name}` : ""}</p>
      {wo.allowed_transitions.includes("in_progress") && (
        <button type="button" disabled={action.isPending}
          onClick={() => action.mutate({ kind: "transition", wo, body: { to: "in_progress", note: "chamado assumido" } }, {
            onSuccess: () => navigate(`/m/work-orders/${wo.id}`),
            onError: (e) => setErr(transitionErrorMessage(e)),
          })}
          className="mt-3 min-h-14 w-full rounded-xl bg-danger-solid text-heading font-bold text-white disabled:opacity-60">
          Assumir chamado
        </button>
      )}
      {err && <p role="alert" className="mt-2 text-caption text-danger">{err}</p>}
    </div>
  );
}

/** Technician — "Minhas OS de hoje" (workflows §3, início do turno). */
export default function MyWorkOrdersPage() {
  const user = useAuthStore((s) => s.user);
  const mine = useMyWorkOrders(user?.id);
  const emergencies = useEmergencyCalls(can(user, "maintenance.execute"));
  const { queue } = useQueueState();
  const today = isoDay();

  const groups = useMemo(() => {
    const list = sortForTechnician((mine.data?.data ?? []).map((w) => applyPending(w, queue)), today);
    const active = list.filter((w) => w.state !== "completed");
    const done = list.filter((w) => w.state === "completed" && dayOf(w.completed_at) === today);
    const sections = SECTION.map((title, i) => ({ title, items: active.filter((w) => Math.min(technicianRank(w, today), 4) === i) }));
    return { sections: sections.filter((s) => s.items.length), done };
  }, [mine.data, queue, today]);
  const calls = (emergencies.data ?? []).filter((w) => !(mine.data?.data ?? []).some((m) => m.id === w.id));

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-title text-fg">Minhas OS</h1>
        <p className="text-caption text-fg-subtle">Emergências primeiro, depois vencidas e as de hoje.</p>
      </div>

      {mine.data?.source === "cache" && (
        <p role="status" data-testid="offline-banner" className="rounded-xl border border-warn-solid/40 bg-warn-solid/10 px-3 py-2 text-caption text-fg">
          Sem conexão — mostrando as OS salvas no aparelho às {formatDateTime(mine.data.saved_at)}. O que você fizer agora é enviado quando a conexão voltar.
        </p>
      )}

      {calls.length > 0 && (
        <section aria-label="Chamados emergenciais" className="space-y-3">
          {calls.map((w) => <EmergencyCard key={w.id} wo={w} />)}
        </section>
      )}

      {mine.isPending ? (
        <LoadingRegion label="Carregando suas OS…"><div className="space-y-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-36 rounded-2xl" />)}</div></LoadingRegion>
      ) : mine.isError ? (
        <ErrorState title="Não foi possível carregar suas OS" error={mine.error} onRetry={() => void mine.refetch()} />
      ) : groups.sections.length === 0 && groups.done.length === 0 ? (
        <EmptyState title="Nenhuma OS atribuída a você" description="Quando o planejador programar uma OS para você, ela aparece aqui (e chega uma notificação)." />
      ) : (
        <>
          {groups.sections.map((s) => (
            <section key={s.title} aria-label={s.title} className="space-y-3">
              <h2 className="text-overline uppercase text-fg-subtle">{s.title} · {s.items.length}</h2>
              <ul className="space-y-3">
                {s.items.map((w) => <li key={w.id}><WoBigCard wo={w} today={today} pending={queue.filter((q) => q.wo_id === w.id).length} /></li>)}
              </ul>
            </section>
          ))}
          {groups.done.length > 0 && (
            <section aria-label="Concluídas hoje" className="space-y-3">
              <h2 className="text-overline uppercase text-fg-subtle">Concluídas hoje · {groups.done.length}</h2>
              <ul className="space-y-3">{groups.done.map((w) => <li key={w.id}><WoBigCard wo={w} today={today} pending={queue.filter((q) => q.wo_id === w.id).length} /></li>)}</ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}
