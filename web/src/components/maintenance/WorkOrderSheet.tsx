import { useEffect, useState, type ReactNode } from "react";
import WoPredictiveBlock from "@/components/predictive/WoPredictiveBlock";
import { Link } from "react-router-dom";
import clsx from "clsx";
import Sheet from "@/components/ui/Sheet";
import Button from "@/components/ui/Button";
import Badge from "@/components/ui/Badge";
import QueryView from "@/components/ui/QueryView";
import { Field, inputClass } from "@/components/ui/Field";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { Progress } from "@/components/ui/LifeBar";
import { BreakInBadge, OverdueBadge, WoStateBadge, WoTypeBadge } from "./badges";
import { transitionErrorMessage } from "./transitionError";
import { useApproveDraft, useTechnicians, useTransition, useUpdateWorkOrder, useWoNames, useWorkOrder } from "@/hooks/useMaintenance";
import { useAuthStore } from "@/store/auth";
import type { CancelReason, TransitionBody, WoState, WorkOrder } from "@/services/maintenanceTypes";
import { formatDate, formatDateTime } from "@/utils/format";
import {
  can, CANCEL_REASON_LABEL, HOLD_REASONS, holdReasonLabel, hoursLabel, isBreakIn, isWoOverdue, isoDay, offeredTransitions,
  remainingHours, transitionLabel, URGENCY_LABEL, WO_STATE_LABEL, WO_TYPE_LABEL,
} from "@/utils/maintenance";
import { REMOVAL_REASON_LABEL } from "@/utils/assets";

function KV({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-caption text-fg-subtle">{k}</dt>
      <dd className="truncate text-body font-medium text-fg">{v}</dd>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2 border-t border-line pt-4">
      <h3 className="text-overline uppercase text-fg-subtle">{title}</h3>
      {children}
    </section>
  );
}

export const CANCEL_REASONS = Object.keys(CANCEL_REASON_LABEL) as CancelReason[];

/** Form for one transition. Calls onSubmit with the transition body. */
export function TransitionForm({ wo, to, busy, error, onSubmit, onCancel, defaultDate, title }: {
  wo: WorkOrder;
  to: WoState;
  title?: string;
  busy: boolean;
  error: string | null;
  onSubmit: (body: TransitionBody) => void;
  onCancel: () => void;
  defaultDate?: string;
}) {
  const techs = useTechnicians(to === "scheduled");
  const [note, setNote] = useState("");
  const [date, setDate] = useState(defaultDate ?? wo.scheduled_for?.slice(0, 10) ?? isoDay(Date.now() + 86_400_000));
  const [assignee, setAssignee] = useState(wo.assignee_id ?? "");
  const [hold, setHold] = useState<string>(HOLD_REASONS[0].value);
  const [remaining, setRemaining] = useState(String(remainingHours(wo)));
  const [cancel, setCancel] = useState<CancelReason | "">("");
  const idp = `tf-${wo.id}`;

  const invalid =
    (to === "scheduled" && !date) ||
    (to === "cancelled" && !cancel) ||
    (to === "on_hold" && (!hold || Number.isNaN(Number(remaining.replace(",", ".")))));

  function submit() {
    if (invalid) return;
    const body: TransitionBody = { to, note: note.trim() || null };
    if (to === "scheduled") {
      body.scheduled_for = date;
      body.assignee_id = assignee || null;
    }
    if (to === "on_hold") {
      body.hold_reason = hold;
      body.remaining_hours = Number(remaining.replace(",", "."));
    }
    if (to === "cancelled") body.cancel_reason = cancel as CancelReason;
    onSubmit(body);
  }

  return (
    <form
      className="space-y-3 rounded-xl border border-line-strong bg-surface-2/40 p-4"
      aria-label={title ?? transitionLabel(wo.state, to)}
      onSubmit={(e) => { e.preventDefault(); submit(); }}
    >
      <p className="text-heading text-fg">{title ?? transitionLabel(wo.state, to)}</p>
      {to === "scheduled" && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Data" htmlFor={`${idp}-date`}>
            <input id={`${idp}-date`} type="date" required value={date} onChange={(e) => setDate(e.target.value)} className={inputClass} />
          </Field>
          <Field label="Técnico responsável" htmlFor={`${idp}-tech`}
            hint={techs.isError ? "Lista de técnicos indisponível — o responsável atual é mantido." : undefined}>
            <select id={`${idp}-tech`} value={assignee} onChange={(e) => setAssignee(e.target.value)} className={inputClass} disabled={techs.isError}>
              <option value="">Sem responsável</option>
              {(techs.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              {wo.assignee_id && !techs.data?.some((t) => t.id === wo.assignee_id) && (
                <option value={wo.assignee_id}>{wo.assignee_name ?? "Responsável atual"}</option>
              )}
            </select>
          </Field>
          {wo.stops_machine && (
            <p className="text-caption text-warn sm:col-span-2">
              Esta OS para a máquina: o supervisor da linha recebe o pedido da janela de parada planejada (PLN-03). Sem a
              janela, a parada conta como não planejada (MNT-02) no OEE.
            </p>
          )}
        </div>
      )}
      {to === "on_hold" && (
        <div className="space-y-3">
          <fieldset>
            <legend className="mb-1.5 text-caption font-medium text-fg-muted">Motivo da pausa</legend>
            <div className="flex flex-wrap gap-2">
              {HOLD_REASONS.map((h) => (
                <label key={h.value} className={clsx("flex min-h-10 cursor-pointer items-center gap-2 rounded-lg border px-3 text-body",
                  hold === h.value ? "border-accent bg-accent/10 text-fg" : "border-line text-fg-muted")}>
                  <input type="radio" name={`${idp}-hold`} value={h.value} checked={hold === h.value} onChange={() => setHold(h.value)} className="accent-[rgb(var(--c-accent))]" />
                  {h.label}
                </label>
              ))}
            </div>
          </fieldset>
          <Field label="Horas restantes estimadas" htmlFor={`${idp}-rem`} hint="Alimenta o backlog (o tempo em espera não conta como horas trabalhadas).">
            <input id={`${idp}-rem`} inputMode="decimal" value={remaining} onChange={(e) => setRemaining(e.target.value)} className={clsx(inputClass, "max-w-[10rem]")} />
          </Field>
        </div>
      )}
      {to === "cancelled" && (
        <Field label="Motivo do cancelamento" htmlFor={`${idp}-cancel`}
          hint={cancel === "superseded" ? "Sai do cumprimento da programação (outra OS substitui esta)." : cancel ? "Conta como não feita no cumprimento da programação." : undefined}>
          <select id={`${idp}-cancel`} required value={cancel} onChange={(e) => setCancel(e.target.value as CancelReason)} className={inputClass}>
            <option value="">Escolha o motivo…</option>
            {CANCEL_REASONS.map((c) => <option key={c} value={c}>{CANCEL_REASON_LABEL[c]}</option>)}
          </select>
        </Field>
      )}
      <Field label="Observação (opcional)" htmlFor={`${idp}-note`}>
        <textarea id={`${idp}-note`} rows={2} value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} className={inputClass} />
      </Field>
      {error && <p role="alert" className="text-caption text-danger">{error}</p>}
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="ghost" onClick={onCancel}>Voltar</Button>
        <Button type="submit" variant={to === "cancelled" ? "danger" : "primary"} loading={busy} disabled={invalid}>
          {title ?? transitionLabel(wo.state, to)}
        </Button>
      </div>
    </form>
  );
}

/** Transitions that need no form (a click is enough, with an optional note). */
const QUICK: WoState[] = ["open", "approved", "in_progress", "closed"];

function Actions({ wo }: { wo: WorkOrder }) {
  const user = useAuthStore((s) => s.user);
  const transition = useTransition();
  const approve = useApproveDraft();
  const update = useUpdateWorkOrder();
  const [form, setForm] = useState<WoState | "reschedule" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const offered = offeredTransitions(wo, user);

  useEffect(() => { setForm(null); setError(null); }, [wo.id, wo.state]);

  async function run(body: TransitionBody) {
    setError(null);
    try {
      const res = await transition.mutateAsync({ id: wo.id, body });
      setForm(null);
      const reset = "plans_reset" in res && Array.isArray(res.plans_reset) && res.plans_reset.length ? " Plano zerado." : "";
      const draft = "new_draft_id" in res && res.new_draft_id ? " O plano continua ≥ 80 %: novo rascunho criado." : "";
      setNotice(`${wo.number}: ${WO_STATE_LABEL[body.to]}.${reset}${draft}`);
    } catch (err) {
      setError(transitionErrorMessage(err));
    }
  }

  /** Reprogram a scheduled WO: PATCH scheduled_for / assignee (keeps scheduled_at — S4-5). */
  async function reschedule(body: TransitionBody) {
    setError(null);
    try {
      await update.mutateAsync({ id: wo.id, body: { scheduled_for: body.scheduled_for ?? undefined, assignee_id: body.assignee_id ?? null } });
      setForm(null);
      setNotice(`${wo.number} reprogramada para ${formatDate(body.scheduled_for)}.`);
    } catch (err) {
      setError(transitionErrorMessage(err));
    }
  }

  /** "Aprovar rascunho" = draft → open → approved in one click (workflows §2). */
  async function approveDraft() {
    setError(null);
    try {
      await approve.mutateAsync(wo.id);
      setNotice(`${wo.number} aprovada.`);
    } catch (err) {
      setError(transitionErrorMessage(err));
    }
  }

  const canReschedule = can(user, "maintenance.plan") && (wo.state === "scheduled" || wo.state === "in_progress");

  const live = <p aria-live="polite" className={clsx("text-caption font-medium text-ok", !notice && "sr-only")}>{notice}</p>;
  if (offered.length === 0 && !canReschedule) {
    return (
      <div className="space-y-1">
        {live}
        <p className="text-caption text-fg-subtle">{wo.state === "closed" || wo.state === "cancelled" ? "OS encerrada — não reabre (abra uma nova OS ligada a esta)." : "Nenhuma ação disponível para o seu perfil neste estado."}</p>
      </div>
    );
  }

  const draftApproval = wo.state === "draft" && offered.includes("open");

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2" role="group" aria-label="Ações da OS">
        {draftApproval && (
          <Button variant="primary" loading={approve.isPending} onClick={() => void approveDraft()}>Aprovar rascunho</Button>
        )}
        {canReschedule && (
          <Button variant="secondary" aria-expanded={form === "reschedule"} onClick={() => setForm(form === "reschedule" ? null : "reschedule")}>Reprogramar</Button>
        )}
        {offered.map((to) => {
          if (draftApproval && to === "open") return null;
          const quick = QUICK.includes(to);
          return (
            <Button key={to}
              variant={to === "cancelled" ? "danger-ghost" : quick && !draftApproval ? "primary" : "secondary"}
              aria-expanded={!quick ? form === to : undefined}
              disabled={transition.isPending}
              onClick={() => (quick ? void run({ to, note: null }) : setForm(form === to ? null : to))}>
              {to === "cancelled" && wo.state === "draft" ? "Descartar" : transitionLabel(wo.state, to)}
            </Button>
          );
        })}
      </div>
      {form === "reschedule" ? (
        <TransitionForm wo={wo} to="scheduled" title="Reprogramar" busy={update.isPending} error={error}
          onSubmit={(b) => void reschedule(b)} onCancel={() => { setForm(null); setError(null); }} />
      ) : form && (
        <TransitionForm wo={wo} to={form} busy={transition.isPending} error={error}
          onSubmit={(b) => void run(b)} onCancel={() => { setForm(null); setError(null); }} />
      )}
      {!form && error && <p role="alert" className="text-caption text-danger">{error}</p>}
      {live}
    </div>
  );
}

export function WorkOrderBody({ wo }: { wo: WorkOrder }) {
  const names = useWoNames();
  const today = isoDay();
  const machine = names.machine(wo);
  const assignee = names.assignee(wo);
  const est = wo.estimated_hours ?? 0;
  const history = [...(wo.history ?? [])].sort((a, b) => b.at.localeCompare(a.at));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <WoStateBadge state={wo.state} />
        <WoTypeBadge type={wo.type} urgency={wo.urgency} />
        {isWoOverdue(wo, today) && <OverdueBadge />}
        {wo.state === "scheduled" && isBreakIn(wo) && <BreakInBadge />}
        {wo.stops_machine && <Badge tone="warn">Para a máquina</Badge>}
      </div>

      <Actions wo={wo} />

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 border-t border-line pt-4">
        <KV k="Máquina" v={wo.machine_id ? <Link className="text-ok underline underline-offset-2" to={`/factory/machines/${wo.machine_id}${wo.position_id ? `?pos=${wo.position_id}` : ""}`}>{machine}</Link> : machine} />
        <KV k="Posição" v={wo.position_name ?? (wo.position_id ? "Posição da máquina" : "Máquina inteira")} />
        <KV k="Tipo" v={`${WO_TYPE_LABEL[wo.type]}${wo.urgency ? ` · ${URGENCY_LABEL[wo.urgency]}` : ""}`} />
        <KV k="Programada para" v={formatDate(wo.scheduled_for)} />
        <KV k="Responsável" v={assignee ?? (wo.assignee_id ? "Técnico atribuído" : "—")} />
        <KV k="Plano" v={wo.plan_id ? <Link className="text-ok underline underline-offset-2" to={`/settings/maintenance-plans?plan=${wo.plan_id}`}>{wo.plan_name ?? "Ver plano"}</Link> : "—"} />
        {wo.hold_reason && wo.state === "on_hold" && <KV k="Motivo da pausa" v={holdReasonLabel(wo.hold_reason)} />}
        {wo.cancel_reason && <KV k="Motivo do cancelamento" v={CANCEL_REASON_LABEL[wo.cancel_reason as CancelReason] ?? wo.cancel_reason} />}
        {wo.failure_id && <KV k="Falha" v={<Link className="text-ok underline underline-offset-2" to={`/maintenance/failures?f=${wo.failure_id}`}>Ver registro de falha</Link>} />}
        {wo.downtime_event_id && <KV k="Parada" v={<Link className="text-ok underline underline-offset-2" to={`/timeline${names.line(wo.machine_id) ? `?line=${names.line(wo.machine_id)?.line_id}` : ""}`}>Ver na linha do tempo</Link>} />}
      </dl>

      <Section title="Horas">
        <div className="grid grid-cols-3 gap-3 text-center">
          <div><p className="text-caption text-fg-subtle">Apontadas</p><p className="text-heading tabular text-fg">{hoursLabel(wo.labor_hours)}</p></div>
          <div><p className="text-caption text-fg-subtle">Estimadas</p><p className="text-heading tabular text-fg">{hoursLabel(wo.estimated_hours)}</p></div>
          <div><p className="text-caption text-fg-subtle">Restantes</p><p className="text-heading tabular text-fg">{hoursLabel(remainingHours(wo))}</p></div>
        </div>
        {est > 0 && <Progress value={wo.labor_hours / est} label="Horas apontadas sobre as estimadas" fillClassName={wo.labor_hours > est ? "bg-warn-solid" : "bg-accent"} size="sm" />}
      </Section>

      {wo.source_alert_id && (
        <Section title={`Alerta preditivo de origem${wo.finding ? ` · achado: ${wo.finding === "defect_confirmed" ? "defeito confirmado" : "sem defeito"}` : ""}`}>
          <WoPredictiveBlock alertId={wo.source_alert_id} showLink />
        </Section>
      )}

      {wo.description && (
        <Section title="Descrição"><p className="whitespace-pre-line text-body text-fg">{wo.description}</p></Section>
      )}

      {wo.task_list.length > 0 && (
        <Section title={`Checklist (${wo.task_list.filter((t) => t.done).length}/${wo.task_list.length})`}>
          <ul className="space-y-1">
            {wo.task_list.map((t) => (
              <li key={t.id} className="flex items-start gap-2 text-body">
                <span aria-hidden="true" className={clsx("mt-0.5 flex size-5 shrink-0 items-center justify-center rounded border",
                  t.done ? "border-ok-solid bg-ok-solid text-white" : "border-line-strong")}>
                  {t.done && <svg viewBox="0 0 20 20" fill="currentColor" className="size-4"><path fillRule="evenodd" d="M16.7 5.3a1 1 0 0 1 0 1.4l-8 8a1 1 0 0 1-1.4 0l-4-4a1 1 0 1 1 1.4-1.4L8 12.6l7.3-7.3a1 1 0 0 1 1.4 0z" clipRule="evenodd" /></svg>}
                </span>
                <span className={clsx(t.done ? "text-fg-muted line-through" : "text-fg")}>{t.text}<span className="sr-only">{t.done ? " (feito)" : " (pendente)"}</span></span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {wo.replacements.length > 0 && (
        <Section title="Troca de componente">
          <ul className="space-y-2">
            {wo.replacements.map((r, i) => (
              <li key={r.id ?? i} className="rounded-lg border border-line p-3 text-body">
                <p className="font-medium text-fg">{r.position_name ?? "Posição"} · {REMOVAL_REASON_LABEL[r.removal_reason as keyof typeof REMOVAL_REASON_LABEL] ?? r.removal_reason}</p>
                <p className="text-caption text-fg-subtle">
                  Saiu: {[r.removed_component?.manufacturer, r.removed_component?.model, r.removed_component?.serial_number].filter(Boolean).join(" · ") || (r.removed_component_id ? "componente instalado" : "—")}
                </p>
                <p className="text-caption text-fg-subtle">
                  Entrou: {[r.new_component?.manufacturer, r.new_component?.model, r.new_component?.serial_number].filter(Boolean).join(" · ") || "componente registrado"}
                </p>
                <p className="text-caption text-fg-subtle">{r.applied_at ? `Aplicada em ${formatDateTime(r.applied_at)}` : "Aplicada na conclusão da OS"}</p>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Trilha de auditoria">
        {history.length === 0 ? <p className="text-caption text-fg-subtle">Sem registros.</p> : (
          <ol className="relative space-y-3 border-l border-line pl-4" aria-label="Histórico de estados">
            {history.map((h, i) => (
              <li key={`${h.at}-${i}`} className="relative">
                <span aria-hidden="true" className="absolute -left-[1.3rem] top-1.5 size-2.5 rounded-full border-2 border-surface bg-accent" />
                <p className="text-body text-fg">
                  {h.from_state ? `${WO_STATE_LABEL[h.from_state] ?? h.from_state} → ` : ""}<strong className="font-semibold">{WO_STATE_LABEL[h.to_state] ?? h.to_state}</strong>
                </p>
                <p className="text-caption text-fg-subtle">{formatDateTime(h.at)} · {h.by_name ?? names.user(h.by) ?? (h.by ? "usuário" : "sistema")}</p>
                {h.note && <p className="text-caption text-fg-muted">“{h.note}”</p>}
              </li>
            ))}
          </ol>
        )}
      </Section>
    </div>
  );
}

/** Detail side sheet of a work order (planner/supervisor desktop). */
export default function WorkOrderSheet({ id, onClose }: { id: string | null; onClose: () => void }) {
  const q = useWorkOrder(id);
  const wo = q.data;
  return (
    <Sheet open={!!id} onClose={onClose} size="lg"
      title={wo ? `${wo.number} · ${wo.title}` : "Ordem de serviço"}
      description={wo ? `Criada em ${formatDateTime(wo.created_at)}` : undefined}>
      <QueryView query={q} errorTitle="Não foi possível carregar a OS"
        loading={<LoadingRegion label="Carregando OS…"><div className="space-y-3"><Skeleton className="h-8 w-48" /><Skeleton className="h-40" /><Skeleton className="h-32" /></div></LoadingRegion>}>
        {(data) => <WorkOrderBody wo={data} />}
      </QueryView>
    </Sheet>
  );
}
