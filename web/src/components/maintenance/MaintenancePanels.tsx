import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Card, CardHeader } from "@/components/ui/Card";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import EmptyState from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { ConsumedBar, OverdueBadge, PlanStateBadge, WoStateBadge } from "./badges";
import CreateWorkOrderSheet from "./CreateWorkOrderSheet";
import { useFailures, usePlanStatus, useWorkOrders } from "@/hooks/useMaintenance";
import { useAuthStore } from "@/store/auth";
import type { PlanStatus, WorkOrder } from "@/services/maintenanceTypes";
import { formatDate, formatDateTime } from "@/utils/format";
import { byTrigger, can, isPastDue, dueTrigger, isActive, isoDay, isWoOverdue, SEVERITY_SHORT, sortPlans } from "@/utils/maintenance";

function PlanLine({ p }: { p: PlanStatus }) {
  const due = dueTrigger(p);
  return (
    <li className="space-y-1 py-2" data-testid="asset-plan">
      <div className="flex items-start justify-between gap-2">
        <span className="min-w-0 text-body font-medium text-fg">{p.plan_name}</span>
        <PlanStateBadge state={p.state} />
      </div>
      <ConsumedBar consumed={p.consumed} state={p.state} trigger={p.determining_trigger} label={`Consumido — ${p.plan_name}`} />
      <p className="text-caption text-fg-subtle">
        {isPastDue(p) ? "Vencido" : p.projected_date ? `Vence ${formatDate(p.projected_date)}${due ? ` ${byTrigger(due.measure)}` : ""}` : "Sem data projetada"}
        {p.position_name ? ` · ${p.position_name}` : ""}
      </p>
    </li>
  );
}

function WoLine({ wo, today }: { wo: WorkOrder; today: string }) {
  return (
    <li className="py-1.5">
      <Link to={`/maintenance/work-orders?wo=${wo.id}`} className="flex items-center justify-between gap-2 rounded-lg px-1 py-1 hover:bg-surface-2" data-testid="asset-wo">
        <span className="min-w-0 truncate text-body text-fg"><span className="font-semibold">{wo.number}</span> · {wo.title}</span>
        <span className="flex shrink-0 items-center gap-1">{isWoOverdue(wo, today) && <OverdueBadge />}<WoStateBadge state={wo.state} /></span>
      </Link>
    </li>
  );
}

/** Machine / position detail: plans, open work orders and recent failures (links into maintenance). */
export function AssetMaintenance({ machineId, positionId, compact = false }: { machineId: string; positionId?: string | null; compact?: boolean }) {
  const user = useAuthStore((s) => s.user);
  const visible = can(user, "maintenance.view") || can(user, "maintenance.request");
  const status = usePlanStatus({}, visible && can(user, "maintenance.view"));
  const wos = useWorkOrders({ machine_id: machineId }, { enabled: visible });
  const failures = useFailures({ machine_id: machineId, ...(positionId ? { position_id: positionId } : {}) }, visible && can(user, "maintenance.failures"));
  const [requesting, setRequesting] = useState(false);
  const today = isoDay();

  const plans = useMemo(() => sortPlans((status.data ?? []).filter((p) => p.machine_id === machineId && (!positionId || p.position_id === positionId)), today), [status.data, machineId, positionId, today]);
  const open = (wos.data ?? []).filter((w) => isActive(w.state) && (!positionId || w.position_id === positionId));
  const recentFailures = (failures.data ?? []).slice().sort((a, b) => b.failed_at.localeCompare(a.failed_at)).slice(0, 3);
  if (!visible) return null;

  const body = (
    <div className={compact ? "space-y-4" : "grid gap-x-8 gap-y-4 lg:grid-cols-2 [&>*]:min-w-0"}>
      {can(user, "maintenance.view") && (
        <div>
          <h4 className="text-overline uppercase text-fg-subtle">Planos</h4>
          {status.isPending ? <Skeleton className="mt-2 h-12" /> : status.isError ? <p className="text-caption text-danger">Não foi possível carregar os planos.</p>
            : plans.length === 0 ? <p className="text-caption text-fg-subtle">Nenhum plano {positionId ? "nesta posição" : "nesta máquina"}.</p>
            : <ul className="divide-y divide-line">{plans.slice(0, compact ? 3 : 6).map((p) => <PlanLine key={p.plan_id} p={p} />)}</ul>}
        </div>
      )}
      <div className="space-y-4">
      <div>
        <h4 className="text-overline uppercase text-fg-subtle">OS abertas</h4>
        {wos.isPending ? <Skeleton className="mt-2 h-10" /> : wos.isError ? <p className="text-caption text-danger">Não foi possível carregar as OS.</p>
          : open.length === 0 ? <p className="text-caption text-fg-subtle">Nenhuma OS aberta.</p>
          : <ul>{open.slice(0, 6).map((w) => <WoLine key={w.id} wo={w} today={today} />)}</ul>}
      </div>
      {can(user, "maintenance.failures") && (
        <div>
          <h4 className="text-overline uppercase text-fg-subtle">Falhas recentes</h4>
          {failures.isPending ? <Skeleton className="mt-2 h-10" /> : recentFailures.length === 0 ? <p className="text-caption text-fg-subtle">Nenhuma falha registrada.</p> : (
            <ul>
              {recentFailures.map((f) => (
                <li key={f.id}>
                  <Link to={`/maintenance/failures?f=${f.id}`} className="flex items-center justify-between gap-2 rounded-lg px-1 py-1.5 text-body hover:bg-surface-2">
                    <span className="text-fg">{formatDateTime(f.failed_at)} · {f.failure_mode ?? "—"}</span>
                    <Badge tone={f.severity === "critical" ? "danger" : f.severity === "degraded" ? "warn" : "info"}>{SEVERITY_SHORT[f.severity]}</Badge>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      </div>
      <div className="flex flex-wrap gap-2 lg:col-span-2">
        {!compact && can(user, "maintenance.request") && <Button size="sm" variant="secondary" onClick={() => setRequesting(true)}>Solicitar manutenção</Button>}
        {can(user, "maintenance.view") && <Link to={`/maintenance/work-orders?machine=${machineId}&view=list`} className="inline-flex min-h-8 items-center px-2 text-caption font-medium text-ok hover:underline">Todas as OS ›</Link>}
      </div>
      {!compact && <CreateWorkOrderSheet open={requesting} onClose={() => setRequesting(false)} machineId={machineId} positionId={positionId ?? null} onCreated={() => undefined} />}
    </div>
  );

  if (compact) return body;
  return (
    <Card>
      <CardHeader title="Manutenção" description="Planos, ordens de serviço e falhas desta máquina." />
      {body}
    </Card>
  );
}

/** Raio-X: overdue work orders (scheduled before today, not completed). */
export function OverdueWorkOrdersCard() {
  const user = useAuthStore((s) => s.user);
  const enabled = can(user, "maintenance.view");
  const wos = useWorkOrders({ overdue: true }, { enabled, refetchMs: 60_000 });
  const today = isoDay();
  if (!enabled) return null;
  const list = (wos.data ?? []).filter((w) => isWoOverdue(w, today));
  return (
    <Card>
      <CardHeader title="OS vencidas" description="Programadas para antes de hoje e ainda não concluídas."
        action={list.length > 0 ? <Badge tone="danger" dot>{list.length}</Badge> : undefined} />
      {wos.isPending ? <Skeleton className="h-24" /> : wos.isError ? <p className="text-caption text-danger">Não foi possível carregar as OS.</p>
        : list.length === 0 ? <EmptyState className="py-6" title="Nenhuma OS vencida" /> : (
          <ul data-testid="raiox-overdue">
            {list.slice(0, 6).map((w) => <WoLine key={w.id} wo={w} today={today} />)}
          </ul>
        )}
      {list.length > 6 && <Link to="/maintenance/work-orders?quick=overdue&view=list" className="mt-2 inline-block text-caption font-medium text-ok hover:underline">Ver todas ({list.length}) ›</Link>}
    </Card>
  );
}
