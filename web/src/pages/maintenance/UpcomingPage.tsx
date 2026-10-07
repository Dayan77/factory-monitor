import { Fragment, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import clsx from "clsx";
import { Card } from "@/components/ui/Card";
import Button from "@/components/ui/Button";
import EmptyState from "@/components/ui/EmptyState";
import QueryView from "@/components/ui/QueryView";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/Table";
import { Progress } from "@/components/ui/LifeBar";
import { ConsumedBar, PlanStateBadge, WoStateBadge } from "@/components/maintenance/badges";
import WorkOrderSheet from "@/components/maintenance/WorkOrderSheet";
import { transitionErrorMessage } from "@/components/maintenance/transitionError";
import { useApproveDraft, usePlanStatus, useWorkOrders } from "@/hooks/useMaintenance";
import { usePlantTree } from "@/hooks/useAssets";
import { useAuthStore } from "@/store/auth";
import type { PlanState, PlanStatus, WorkOrder } from "@/services/maintenanceTypes";
import { formatDate, formatPct } from "@/utils/format";
import {
  byTrigger, can, daysToDue, isPastDue, reliabilityReason, dueTrigger, isoDay, nextDueUsageLabel, sortPlans, TRIGGER_LABEL,
  triggerUsageLabel,
} from "@/utils/maintenance";
import { AssetFilters, Pills, useUrlFilters } from "./filters";

const STATES: PlanState[] = ["overdue", "due", "due_soon", "ok"];
const COUNT_TEXT: Record<PlanState, (n: number) => string> = {
  overdue: (n) => (n === 1 ? "plano além da tolerância" : "planos além da tolerância"),
  due: (n) => (n === 1 ? "plano vencido" : "planos vencidos"),
  due_soon: (n) => (n === 1 ? "plano vence em breve (≥ 80 %)" : "planos vencem em breve (≥ 80 %)"),
  ok: (n) => (n === 1 ? "plano em dia" : "planos em dia"),
};
const PERIODS = [
  { value: "all", label: "Todos" },
  { value: "7", label: "Esta semana" },
  { value: "30", label: "30 dias" },
] as const;
type Period = (typeof PERIODS)[number]["value"];
const FILTER_KEYS = ["plant", "line", "machine", "state", "period", "wo"] as const;

function DueCell({ p, today }: { p: PlanStatus; today: string }) {
  const due = dueTrigger(p);
  const days = daysToDue(p, today);
  return (
    <div>
      <p className={clsx("font-medium tabular", isPastDue(p) ? "text-danger" : "text-fg")}>
        {isPastDue(p) ? "Vencido" : formatDate(p.projected_date)}
      </p>
      <p className="text-caption text-fg-subtle">
        {isPastDue(p) ? `${formatPct(p.consumed)} do intervalo` : days == null ? `sem projeção (${reliabilityReason(p.reason ?? "no_recent_usage")})` : days === 0 ? "vence hoje" : `em ${days} dia${days > 1 ? "s" : ""}`}
        {due && !isPastDue(p) && ` · ${byTrigger(due.measure)}`}
      </p>
      {p.low_confidence && !isPastDue(p) && (
        <p className="text-caption text-warn" data-testid="plan-low-confidence" title="Ritmo de uso calculado com menos de 14 dias de dados (kpis.md §4.2)">
          baixa confiança: menos de 14 dias de dados
        </p>
      )}
    </div>
  );
}

function Triggers({ p }: { p: PlanStatus }) {
  const due = dueTrigger(p);
  return (
    <ul className="space-y-1.5" aria-label={`Gatilhos de ${p.plan_name}`}>
      {p.triggers.map((t) => (
        <li key={t.measure} className="grid grid-cols-[5.5rem_1fr_auto] items-center gap-2 text-caption">
          <span className={clsx("text-fg-muted", t.measure === p.determining_trigger && "font-semibold text-fg")}>{TRIGGER_LABEL[t.measure]}</span>
          <Progress value={t.consumed} size="sm" label={`${TRIGGER_LABEL[t.measure]}: ${formatPct(t.consumed)}`}
            fillClassName={t.consumed >= 1 ? "bg-danger-solid" : t.consumed >= 0.8 ? "bg-warn-solid" : "bg-ok-solid"} />
          <span className="tabular text-fg-subtle">
            {triggerUsageLabel(t)}{t.projected_date ? ` · ${formatDate(t.projected_date)}` : ""}{due?.measure === t.measure ? " ◂ vence primeiro" : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}

function WoChip({ wo, p, onOpen }: { wo: WorkOrder | undefined; p: PlanStatus; onOpen: (id: string) => void }) {
  const state = wo?.state ?? p.open_work_order_state ?? null;
  return (
    <button type="button" onClick={() => onOpen(p.open_work_order_id as string)} className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-line px-2.5 text-caption hover:bg-surface-2">
      <span className="whitespace-nowrap font-semibold text-fg">{wo?.number ?? p.open_work_order_number ?? "OS"}</span>
      {state && <WoStateBadge state={state} />}
      {wo?.scheduled_for && <span className="text-fg-subtle">{formatDate(wo.scheduled_for)}</span>}
    </button>
  );
}

function ApproveDraft({ wo }: { wo: Pick<WorkOrder, "id"> }) {
  const t = useApproveDraft();
  const [err, setErr] = useState<string | null>(null);
  async function go() {
    setErr(null);
    try {
      await t.mutateAsync(wo.id);
    } catch (e) {
      setErr(transitionErrorMessage(e));
    }
  }
  return (
    <span className="inline-flex flex-col">
      <Button size="sm" variant="primary" loading={t.isPending} onClick={() => void go()}>Aprovar rascunho</Button>
      {err && <span role="alert" className="mt-1 max-w-[14rem] text-caption text-danger">{err}</span>}
    </span>
  );
}

function PlanRows({ list, wos, today, onOpen, planner }: { list: PlanStatus[]; wos: Map<string, WorkOrder>; today: string; onOpen: (id: string) => void; planner: boolean }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  return (
    <>
      <div className="hidden lg:block">
        <Table caption="Planos de manutenção por vencimento">
          <THead>
            <tr>
              <TH>Plano</TH>
              <TH>Estado</TH>
              <TH className="w-64">Consumido (gatilho)</TH>
              <TH>Vencimento</TH>
              <TH>Próximo uso</TH>
              <TH>OS do plano</TH>
            </tr>
          </THead>
          <TBody>
            {list.map((p) => {
              const wo = p.open_work_order_id ? wos.get(p.open_work_order_id) : undefined;
              const open = expanded === p.plan_id;
              return (
                <Fragment key={p.plan_id}>
                  <TR data-testid="plan-row" data-plan={p.plan_id}>
                    <TD>
                      <button type="button" aria-expanded={open} onClick={() => setExpanded(open ? null : p.plan_id)} className="text-left">
                        <span className="block font-medium text-fg hover:underline">{p.plan_name}</span>
                        <span className="block text-caption text-fg-subtle">{p.machine_name ?? "—"}{p.position_name ? ` · ${p.position_name}` : " · máquina inteira"}</span>
                      </button>
                    </TD>
                    <TD><PlanStateBadge state={p.state} /></TD>
                    <TD><ConsumedBar consumed={p.consumed} state={p.state} trigger={p.determining_trigger} label={`Consumido — ${p.plan_name}`} /></TD>
                    <TD><DueCell p={p} today={today} /></TD>
                    <TD className="text-caption text-fg-muted">{nextDueUsageLabel(p.next_due_usage) ?? "—"}</TD>
                    <TD>
                      {p.open_work_order_id ? (
                        <div className="flex flex-wrap items-center gap-2">
                          <WoChip wo={wo} p={p} onOpen={onOpen} />
                          {planner && (wo?.state ?? p.open_work_order_state) === "draft" && <ApproveDraft wo={{ id: p.open_work_order_id }} />}
                        </div>
                      ) : <span className="text-caption text-fg-subtle">—</span>}
                    </TD>
                  </TR>
                  {open && (
                    <tr><td colSpan={6} className="bg-surface-2/40 px-4 py-3"><Triggers p={p} /></td></tr>
                  )}
                </Fragment>
              );
            })}
          </TBody>
        </Table>
      </div>
      <ul className="space-y-3 lg:hidden">
        {list.map((p) => {
          const wo = p.open_work_order_id ? wos.get(p.open_work_order_id) : undefined;
          return (
            <li key={p.plan_id} data-testid="plan-card" className="rounded-xl border border-line bg-surface p-4 shadow-card">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="font-medium text-fg">{p.plan_name}</p>
                  <p className="text-caption text-fg-subtle">{p.machine_name ?? "—"}{p.position_name ? ` · ${p.position_name}` : ""}</p>
                </div>
                <PlanStateBadge state={p.state} />
              </div>
              <ConsumedBar className="mt-3" consumed={p.consumed} state={p.state} trigger={p.determining_trigger} label={`Consumido — ${p.plan_name}`} />
              <div className="mt-3 flex flex-wrap items-end justify-between gap-2">
                <DueCell p={p} today={today} />
                {nextDueUsageLabel(p.next_due_usage) && <p className="text-caption text-fg-muted">vence em {nextDueUsageLabel(p.next_due_usage)}</p>}
              </div>
              <details className="mt-2">
                <summary className="min-h-9 cursor-pointer py-2 text-caption font-medium text-ok">Gatilhos</summary>
                <Triggers p={p} />
              </details>
              {p.open_work_order_id && (
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <WoChip wo={wo} p={p} onOpen={onOpen} />
                  {planner && (wo?.state ?? p.open_work_order_state) === "draft" && <ApproveDraft wo={{ id: p.open_work_order_id }} />}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </>
  );
}

/** Planner 07:30 — "O que vence e o que está atrasado?" (workflows §2). */
export default function UpcomingPage() {
  const { values: f, set } = useUrlFilters(FILTER_KEYS);
  const tree = usePlantTree();
  const user = useAuthStore((s) => s.user);
  const planner = can(user, "maintenance.plan");
  const plantId = f.plant || tree.data?.[0]?.id || undefined;
  const status = usePlanStatus({ plant_id: plantId, line_id: f.line || undefined }, tree.isSuccess);
  // only the active WOs of the plans (number/state also come in the status rows)
  const wos = useWorkOrders({ state: undefined }, { refetchMs: 60_000 });
  const today = isoDay();
  const period = (f.period || "all") as Period;
  const state = f.state as PlanState | "";

  const woMap = useMemo(() => new Map((wos.data ?? []).map((w) => [w.id, w])), [wos.data]);
  const scoped = useMemo(() => {
    let list = status.data ?? [];
    if (f.machine) list = list.filter((p) => p.machine_id === f.machine);
    if (period !== "all") {
      const n = Number(period);
      list = list.filter((p) => { const d = daysToDue(p, today); return d != null && d <= n; });
    }
    return sortPlans(list, today);
  }, [status.data, f.machine, period, today]);
  const counts = useMemo(() => Object.fromEntries(STATES.map((s) => [s, scoped.filter((p) => p.state === s).length])) as Record<PlanState, number>, [scoped]);
  const list = state ? scoped.filter((p) => p.state === state) : scoped;

  return (
    <div className="mx-auto max-w-7xl space-y-5 animate-rise-in">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-title text-fg">Próximas manutenções</h2>
          <p className="text-caption text-fg-subtle">Planos por vencimento: o gatilho que vier primeiro (ciclos, horas, partidas ou calendário).</p>
        </div>
        {planner && <Link to="/settings/maintenance-plans" className="inline-flex min-h-10 items-center rounded-lg border border-line-strong bg-surface px-4 text-body font-medium text-fg hover:bg-surface-2">Cadastro de planos</Link>}
      </div>

      <section aria-label="Resumo por estado" className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {STATES.map((s) => (
          <button key={s} type="button" aria-pressed={state === s} data-testid={`count-${s}`}
            onClick={() => set({ state: state === s ? null : s })}
            className={clsx("rounded-xl border bg-surface p-4 text-left shadow-card transition-colors hover:border-line-strong",
              state === s ? "border-accent ring-2 ring-accent/30" : "border-line")}>
            <PlanStateBadge state={s} />
            <p className="mt-2 text-display tabular text-fg">{status.isPending ? "…" : counts[s]}</p>
            <p className="text-caption text-fg-subtle">{COUNT_TEXT[s](counts[s])}</p>
          </button>
        ))}
      </section>

      <Card padded={false} className="p-4">
        <div className="flex flex-wrap items-center gap-3">
          {tree.data && (
            <AssetFilters tree={tree.data} plant={plantId ?? ""} line={f.line} machine={f.machine}
              onChange={(p) => set({ plant: p.plant, line: p.line, machine: p.machine })} />
          )}
          <Pills label="Período" items={PERIODS} value={period} onChange={(v) => set({ period: v === "all" ? null : v })} />
        </div>
      </Card>

      <Card padded={false} className="p-0 sm:p-2">
        <QueryView query={status} errorTitle="Não foi possível carregar os planos"
          loading={<LoadingRegion label="Calculando vencimentos…"><div className="space-y-2 p-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-14" />)}</div></LoadingRegion>}
          isEmpty={() => list.length === 0}
          empty={<EmptyState title={status.data?.length ? "Nenhum plano com esses filtros" : "Nenhum plano de manutenção cadastrado"}
            description={status.data?.length ? "Limpe os filtros para ver todos." : "Cadastre planos por ciclos, horas, partidas ou calendário."}
            action={!status.data?.length && planner ? <Link to="/settings/maintenance-plans" className="text-body font-medium text-ok hover:underline">Cadastrar plano</Link> : undefined} />}>
          {() => (
            <div className="p-2 sm:p-0">
              <p className="mb-2 px-2 text-caption text-fg-subtle">{list.length} plano{list.length > 1 ? "s" : ""}, ordenados pela data de vencimento</p>
              <PlanRows list={list} wos={woMap} today={today} planner={planner} onOpen={(id) => set({ wo: id }, false)} />
            </div>
          )}
        </QueryView>
      </Card>

      <WorkOrderSheet id={f.wo || null} onClose={() => set({ wo: null }, false)} />
    </div>
  );
}
