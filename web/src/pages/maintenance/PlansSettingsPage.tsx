import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import clsx from "clsx";
import { Card } from "@/components/ui/Card";
import Button from "@/components/ui/Button";
import Badge from "@/components/ui/Badge";
import Sheet from "@/components/ui/Sheet";
import EmptyState from "@/components/ui/EmptyState";
import QueryView from "@/components/ui/QueryView";
import { Field, inputClass } from "@/components/ui/Field";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/Table";
import { ConsumedBar, PlanStateBadge } from "@/components/maintenance/badges";
import { useDeletePlan, useMachineIndex, usePlanStatus, usePlans, usePositionsIndex, useSavePlan } from "@/hooks/useMaintenance";
import { usePosition } from "@/hooks/useAssets";
import type { MaintenancePlan, MaintenancePlanBody, PlanStatus, PlanWoType } from "@/services/maintenanceTypes";
import { apiErrorMessage } from "@/utils/errors";
import { formatDate, formatDec1, formatInt } from "@/utils/format";
import { byTrigger, dueTrigger, hoursLabel, nextDueUsageLabel } from "@/utils/maintenance";
import { useUrlFilters } from "./filters";

type Draft = {
  machine_id: string;
  position_id: string;
  name: string;
  tasks: string[];
  estimated_hours: string;
  wo_type: PlanWoType;
  interval_cycles: string;
  interval_hours: string;
  interval_starts: string;
  interval_days: string;
  tolerance: string;
  reset_on_replacement: boolean;
  stops_machine: boolean;
  is_active: boolean;
  /** Plan that already existed on paper: date of the last execution (create only). */
  last_done_at: string;
};

const EMPTY: Draft = {
  machine_id: "", position_id: "", name: "", tasks: [""], estimated_hours: "1", wo_type: "preventive",
  interval_cycles: "", interval_hours: "", interval_starts: "", interval_days: "", tolerance: "10",
  reset_on_replacement: true, stops_machine: false, is_active: true, last_done_at: "",
};

const num = (s: string) => (s.trim() === "" ? null : Number(s.replace(/\./g, "").replace(",", ".")));
const dec = (s: string) => (s.trim() === "" ? null : Number(s.replace(",", ".")));

function fromPlan(p: MaintenancePlan, machineOfPosition: (id: string) => string | undefined): Draft {
  return {
    machine_id: p.machine_id ?? (p.position_id ? machineOfPosition(p.position_id) ?? "" : ""),
    position_id: p.position_id ?? "",
    name: p.name,
    tasks: p.task_list.length ? p.task_list.map((t) => t.text) : [""],
    estimated_hours: String(p.estimated_hours ?? ""),
    wo_type: p.wo_type,
    interval_cycles: p.interval_cycles?.toString() ?? "",
    interval_hours: p.interval_hours?.toString() ?? "",
    interval_starts: p.interval_starts?.toString() ?? "",
    interval_days: p.interval_days?.toString() ?? "",
    tolerance: p.tolerance_pct == null ? "10" : String(p.tolerance_pct).replace(".", ","),
    reset_on_replacement: p.reset_on_replacement,
    stops_machine: p.stops_machine,
    is_active: p.is_active,
    last_done_at: "",
  };
}

/** Validation: name, anchor, ≥ 1 trigger, positive numbers. Exported for tests. */
export function validatePlan(d: Draft): string | null {
  if (d.name.trim().length < 3) return "Informe o nome do plano.";
  if (!d.machine_id) return "Escolha a máquina (e a posição, se o plano for de um componente).";
  const triggers = [d.interval_cycles, d.interval_hours, d.interval_starts, d.interval_days].map(num);
  if (triggers.every((t) => t == null)) return "Informe pelo menos um gatilho (ciclos, horas, partidas ou dias).";
  if (triggers.some((t) => t != null && (Number.isNaN(t) || t <= 0))) return "Os intervalos devem ser números maiores que zero.";
  const h = dec(d.estimated_hours);
  if (h == null || Number.isNaN(h) || h <= 0) return "Informe as horas estimadas.";
  const tol = dec(d.tolerance);
  if (tol == null || Number.isNaN(tol) || tol < 0 || tol > 100) return "Tolerância entre 0 e 100 %.";
  if (d.last_done_at && d.last_done_at > new Date().toISOString().slice(0, 10)) return "A última execução não pode estar no futuro.";
  return null;
}

export function toBody(d: Draft, plan?: MaintenancePlan): MaintenancePlanBody {
  const texts = d.tasks.map((t) => t.trim()).filter(Boolean);
  return {
    machine_id: d.position_id ? null : d.machine_id,
    position_id: d.position_id || null,
    name: d.name.trim(),
    task_list: texts.map((text, i) => {
      const same = plan?.task_list.find((t) => t.text === text) ?? plan?.task_list[i];
      return same && same.text === text ? { id: same.id, text } : { text };
    }),
    estimated_hours: dec(d.estimated_hours) as number,
    wo_type: d.wo_type,
    interval_cycles: num(d.interval_cycles),
    interval_hours: dec(d.interval_hours),
    interval_starts: num(d.interval_starts),
    interval_days: num(d.interval_days),
    tolerance_pct: dec(d.tolerance) ?? 10,
    reset_on_replacement: d.reset_on_replacement,
    stops_machine: d.stops_machine,
    is_active: d.is_active,
    ...(!plan && d.last_done_at ? { last_done_at: `${d.last_done_at}T12:00:00` } : {}),
  };
}

function PositionUsageHint({ positionId }: { positionId: string }) {
  const q = usePosition(positionId || null);
  if (!positionId) return null;
  if (q.isPending) return <Skeleton className="h-4 w-64" />;
  if (!q.data) return null;
  const u = q.data.usage;
  return (
    <p className="text-caption text-fg-subtle" data-testid="position-usage">
      Uso atual da posição: {formatInt(u.cycles_total)} ciclos · {formatDec1(u.hours_total)} h · {formatInt(u.starts_total)} partidas
    </p>
  );
}

function PlanSheet({ open, plan, onClose, status }: { open: boolean; plan: MaintenancePlan | null; onClose: () => void; status?: PlanStatus }) {
  const { tree, index } = useMachineIndex();
  const [d, setD] = useState<Draft>(EMPTY);
  const [touched, setTouched] = useState(false);
  const save = useSavePlan();
  const del = useDeletePlan();
  const machineIds = useMemo(() => (d.machine_id ? [d.machine_id] : []), [d.machine_id]);
  const positions = usePositionsIndex(machineIds);
  const allPositions = usePositionsIndex(useMemo(() => (plan?.position_id && !plan.machine_id ? [...index.keys()] : []), [plan, index]));
  const positionList = [...positions.values()].filter((p) => p.machine_id === d.machine_id);

  useEffect(() => {
    if (!open) return;
    setD(plan ? fromPlan(plan, (id) => allPositions.get(id)?.machine_id) : EMPTY);
    setTouched(false);
    save.reset();
  }, [open, plan?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // the position's machine arrives after the positions index loads
  useEffect(() => {
    if (open && plan?.position_id && !d.machine_id) {
      const m = allPositions.get(plan.position_id)?.machine_id;
      if (m) setD((x) => ({ ...x, machine_id: m }));
    }
  }, [allPositions, open, plan, d.machine_id]);

  const error = validatePlan(d);
  const up = (patch: Partial<Draft>) => setD((x) => ({ ...x, ...patch }));

  function submit() {
    setTouched(true);
    if (error) return;
    save.mutate({ id: plan?.id, body: toBody(d, plan ?? undefined) }, { onSuccess: onClose });
  }

  const trig = (key: "interval_cycles" | "interval_hours" | "interval_starts" | "interval_days", label: string, unit: string) => (
    <Field label={label} htmlFor={`pl-${key}`}>
      <div className="flex items-center gap-2">
        <input id={`pl-${key}`} inputMode="decimal" value={d[key]} onChange={(e) => up({ [key]: e.target.value })} className={inputClass} placeholder="—" />
        <span className="shrink-0 text-caption text-fg-subtle">{unit}</span>
      </div>
    </Field>
  );

  return (
    <Sheet open={open} onClose={onClose} size="lg" title={plan ? "Editar plano" : "Novo plano de manutenção"}
      description="Vence pelo gatilho que vier primeiro. Rascunho de OS é criado ao atingir 80 %."
      footer={
        <div className="space-y-2">
          {touched && error && <p role="alert" className="text-caption text-danger">{error}</p>}
          {save.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(save.error, "Não foi possível salvar o plano.")}</p>}
          {del.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(del.error, "Não foi possível excluir o plano.")}</p>}
          <div className="flex flex-wrap justify-between gap-2">
            {plan ? (
              <Button variant="danger-ghost" loading={del.isPending}
                onClick={() => { if (window.confirm(`Excluir o plano “${plan.name}”? O histórico de OS é mantido.`)) del.mutate(plan.id, { onSuccess: onClose }); }}>
                Excluir plano
              </Button>
            ) : <span />}
            <Button variant="primary" loading={save.isPending} onClick={submit}>{plan ? "Salvar alterações" : "Criar plano"}</Button>
          </div>
        </div>
      }>
      <form className="space-y-5" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        {status && (
          <div className="rounded-xl border border-line bg-surface-2/40 p-3" data-testid="plan-live-status">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <PlanStateBadge state={status.state} />
              <span className="text-caption text-fg-subtle">
                {status.projected_date ? `vence ${formatDate(status.projected_date)}` : "sem data projetada"}
                {dueTrigger(status) && ` ${byTrigger(dueTrigger(status)!.measure)}`}
              </span>
            </div>
            <ConsumedBar className="mt-2" consumed={status.consumed} state={status.state} trigger={status.determining_trigger} label="Consumido agora" />
            {nextDueUsageLabel(status.next_due_usage) && <p className="mt-1 text-caption text-fg-muted">Próximo vencimento em {nextDueUsageLabel(status.next_due_usage)}</p>}
          </div>
        )}
        <Field label="Nome" htmlFor="pl-name">
          <input id="pl-name" value={d.name} onChange={(e) => up({ name: e.target.value })} maxLength={200} className={inputClass} placeholder="Ex.: troca de vedações — cilindro A1" />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Máquina" htmlFor="pl-machine">
            <select id="pl-machine" value={d.machine_id} onChange={(e) => up({ machine_id: e.target.value, position_id: "" })} className={inputClass}>
              <option value="">Escolha…</option>
              {(tree.data ?? []).flatMap((p) => p.lines).map((l) => (
                <optgroup key={l.id} label={l.name}>{l.machines.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</optgroup>
              ))}
            </select>
          </Field>
          <Field label="Posição" htmlFor="pl-position" hint="Vazio = plano da máquina inteira (não zera na troca de componente).">
            <select id="pl-position" value={d.position_id} onChange={(e) => up({ position_id: e.target.value })} className={inputClass} disabled={!d.machine_id}>
              <option value="">Máquina inteira</option>
              {positionList.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </Field>
        </div>
        <PositionUsageHint positionId={d.position_id} />

        <fieldset className="space-y-2">
          <legend className="text-caption font-medium text-fg-muted">Gatilhos (o que vier primeiro) — pelo menos um</legend>
          <div className="grid gap-3 sm:grid-cols-2">
            {trig("interval_cycles", "A cada (ciclos)", "ciclos")}
            {trig("interval_hours", "A cada (horas de operação)", "h")}
            {trig("interval_starts", "A cada (partidas)", "partidas")}
            {trig("interval_days", "A cada (dias corridos)", "dias")}
          </div>
        </fieldset>

        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Horas estimadas" htmlFor="pl-hours">
            <input id="pl-hours" inputMode="decimal" value={d.estimated_hours} onChange={(e) => up({ estimated_hours: e.target.value })} className={inputClass} />
          </Field>
          <Field label="Tipo de OS" htmlFor="pl-type">
            <select id="pl-type" value={d.wo_type} onChange={(e) => up({ wo_type: e.target.value as PlanWoType })} className={inputClass}>
              <option value="preventive">Preventiva</option>
              <option value="predictive">Preditiva</option>
            </select>
          </Field>
          <Field label="Tolerância de atraso (%)" htmlFor="pl-tol" hint="Acima de 100 % + tolerância = atrasado.">
            <input id="pl-tol" inputMode="decimal" value={d.tolerance} onChange={(e) => up({ tolerance: e.target.value })} className={inputClass} />
          </Field>
        </div>

        {!plan && (
          <Field label="Última execução (opcional)" htmlFor="pl-last" hint="Para planos que já existiam no papel: o plano conta a partir desta data (uso da posição nessa data). Vazio = a partir de agora.">
            <input id="pl-last" type="date" value={d.last_done_at} max={new Date().toISOString().slice(0, 10)} onChange={(e) => up({ last_done_at: e.target.value })} className={clsx(inputClass, "max-w-[12rem]")} />
          </Field>
        )}

        <fieldset className="space-y-2">
          <legend className="text-caption font-medium text-fg-muted">Checklist</legend>
          <ol className="space-y-2">
            {d.tasks.map((t, i) => (
              <li key={i} className="flex items-center gap-2">
                <span className="w-6 shrink-0 text-right text-caption tabular text-fg-subtle">{i + 1}.</span>
                <input aria-label={`Item ${i + 1} do checklist`} value={t} onChange={(e) => up({ tasks: d.tasks.map((x, j) => (j === i ? e.target.value : x)) })}
                  className={inputClass} placeholder="Ex.: verificar vazamentos" />
                <Button variant="ghost" size="sm" aria-label={`Remover item ${i + 1}`} disabled={d.tasks.length === 1}
                  onClick={() => up({ tasks: d.tasks.filter((_, j) => j !== i) })}>✕</Button>
              </li>
            ))}
          </ol>
          <Button size="sm" variant="secondary" onClick={() => up({ tasks: [...d.tasks, ""] })}>Adicionar item</Button>
        </fieldset>

        <div className="space-y-2">
          {([
            ["reset_on_replacement", "Zerar o plano na troca do componente da posição"],
            ["stops_machine", "A execução para a máquina (pede janela de parada à produção)"],
            ["is_active", "Plano ativo"],
          ] as const).map(([k, label]) => (
            <label key={k} className="flex min-h-11 cursor-pointer items-center gap-3 rounded-lg border border-line px-3">
              <input type="checkbox" checked={d[k]} onChange={(e) => up({ [k]: e.target.checked })} className="size-5 accent-[rgb(var(--c-accent))]" />
              <span className="text-body text-fg">{label}</span>
            </label>
          ))}
        </div>
      </form>
    </Sheet>
  );
}

function triggersText(p: MaintenancePlan): string {
  const parts = [
    p.interval_cycles && `${formatInt(p.interval_cycles)} ciclos`,
    p.interval_hours && `${formatInt(p.interval_hours)} h`,
    p.interval_starts && `${formatInt(p.interval_starts)} partidas`,
    p.interval_days && `${formatInt(p.interval_days)} dias`,
  ].filter(Boolean);
  return parts.length ? parts.join(" ou ") : "—";
}

/** Planner: maintenance plans (cadastro) — workflows §2 "Cadastro". */
export default function PlansSettingsPage() {
  const { values: f, set } = useUrlFilters(["plan", "new"] as const);
  const plans = usePlans();
  const status = usePlanStatus({});
  const { index } = useMachineIndex();
  const statusById = useMemo(() => new Map((status.data ?? []).map((s) => [s.plan_id, s])), [status.data]);
  const editing = f.plan ? plans.data?.find((p) => p.id === f.plan) ?? null : null;
  const open = !!f.new || !!editing;

  return (
    <div className="mx-auto max-w-7xl space-y-5 animate-rise-in">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-title text-fg">Planos de manutenção</h2>
          <p className="text-caption text-fg-subtle">Planos por posição (componente) ou máquina inteira, com um ou mais gatilhos.</p>
        </div>
        <div className="flex gap-2">
          <Link to="/maintenance/upcoming" className="inline-flex min-h-10 items-center rounded-lg border border-line-strong bg-surface px-4 text-body font-medium text-fg hover:bg-surface-2">Próximas manutenções</Link>
          <Button variant="primary" onClick={() => set({ new: "1", plan: null }, false)}>Novo plano</Button>
        </div>
      </div>
      <Card padded={false} className="p-2">
        <QueryView query={plans} errorTitle="Não foi possível carregar os planos"
          loading={<LoadingRegion label="Carregando planos…"><div className="space-y-2 p-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-12" />)}</div></LoadingRegion>}
          isEmpty={(d) => d.length === 0}
          empty={<EmptyState title="Nenhum plano cadastrado" description="Crie o primeiro plano: ex. trocar vedações a cada 2 000 000 ciclos ou 180 dias." action={<Button variant="primary" onClick={() => set({ new: "1" }, false)}>Novo plano</Button>} />}>
          {(list) => (
            <Table caption="Planos de manutenção">
              <THead><tr><TH>Plano</TH><TH>Gatilhos</TH><TH>Tipo</TH><TH numeric>Horas</TH><TH className="w-56">Agora</TH><TH>Situação</TH></tr></THead>
              <TBody>
                {list.map((p) => {
                  const st = statusById.get(p.id);
                  return (
                    <TR key={p.id} interactive tabIndex={0} data-testid="plan-settings-row" onClick={() => set({ plan: p.id, new: null }, false)}
                      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); set({ plan: p.id, new: null }, false); } }}>
                      <TD>
                        <p className="font-medium text-fg">{p.name}</p>
                        <p className="text-caption text-fg-subtle">{st?.machine_name ?? (p.machine_id ? index.get(p.machine_id)?.name : null) ?? "—"}{st?.position_name ? ` · ${st.position_name}` : p.position_id ? "" : " · máquina inteira"} · {p.task_list.length} itens</p>
                      </TD>
                      <TD className="text-caption">{triggersText(p)}</TD>
                      <TD className="text-caption">{p.wo_type === "predictive" ? "Preditiva" : "Preventiva"}</TD>
                      <TD numeric>{hoursLabel(p.estimated_hours)}</TD>
                      <TD>{st ? <ConsumedBar consumed={st.consumed} state={st.state} trigger={st.determining_trigger} label={`Consumido — ${p.name}`} /> : <span className="text-caption text-fg-subtle">{status.isPending ? "…" : "—"}</span>}</TD>
                      <TD>
                        <div className={clsx("flex flex-wrap gap-1")}>
                          {st && <PlanStateBadge state={st.state} />}
                          {!p.is_active && <Badge>Inativo</Badge>}
                          {p.stops_machine && <Badge tone="warn">Para a máquina</Badge>}
                        </div>
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          )}
        </QueryView>
      </Card>
      <PlanSheet open={open} plan={editing} status={editing ? statusById.get(editing.id) : undefined} onClose={() => set({ plan: null, new: null }, false)} />
    </div>
  );
}
