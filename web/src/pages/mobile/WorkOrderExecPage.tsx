import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import clsx from "clsx";
import Badge from "@/components/ui/Badge";
import Sheet from "@/components/ui/Sheet";
import ErrorState from "@/components/ui/ErrorState";
import EmptyState from "@/components/ui/EmptyState";
import { Field, inputClass } from "@/components/ui/Field";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { Progress } from "@/components/ui/LifeBar";
import { OverdueBadge, WoStateBadge, WoTypeBadge } from "@/components/maintenance/badges";
import FailureForm from "@/components/maintenance/FailureForm";
import WoPredictiveBlock from "@/components/predictive/WoPredictiveBlock";
import { useAlert } from "@/hooks/usePredictive";
import { COMPATIBLE_MODES } from "@/utils/predictive";
import type { WoFinding } from "@/services/maintenanceTypes";
import { transitionErrorMessage } from "@/components/maintenance/transitionError";
import { BigChoice, HoursStepper, ScanInput } from "@/components/mobile/inputs";
import { useEffectiveWorkOrder, useOnline, useQueueState, useTechAction, useTechWorkOrder } from "@/hooks/useTechnician";
import { useCreateWorkOrder, useFailure, usePatchFailure, useReplacement, useWoNames } from "@/hooks/useMaintenance";
import { usePosition } from "@/hooks/useAssets";
import { componentsApi } from "@/services/api";
import { failuresApi } from "@/services/maintenanceApi";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuthStore } from "@/store/auth";
import type { FailureSeverity, WoState, WorkOrder } from "@/services/maintenanceTypes";
import type { RemovalReason } from "@/services/types";
import { apiErrorMessage } from "@/utils/errors";
import { formatDateTime, formatInt } from "@/utils/format";
import { REMOVAL_REASON_LABEL } from "@/utils/assets";
import {
  HOLD_REASONS, holdReasonLabel, hoursLabel, isoDay, isWoOverdue, offeredTransitions, remainingHours, WO_STATE_LABEL,
} from "@/utils/maintenance";

type SheetKind = "pause" | "complete" | "labor" | "replace" | "failure" | "problem" | null;

function Block({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="space-y-3 rounded-2xl border border-line bg-surface p-4 shadow-card">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-heading text-fg">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

/** Hours since the WO last entered in_progress (prefill of "horas trabalhadas"). */
function elapsedSinceResume(wo: WorkOrder, now: number): number {
  const last = [...(wo.history ?? [])].reverse().find((h) => h.to_state === "in_progress")?.at ?? wo.started_at;
  if (!last) return 0;
  const h = (now - Date.parse(last)) / 3_600_000;
  return Math.max(0.25, Math.round(h * 4) / 4);
}

function useNotice() {
  const [notice, setNotice] = useState<{ tone: "ok" | "info" | "danger"; text: string } | null>(null);
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), 6000);
    return () => clearTimeout(t);
  }, [notice]);
  return [notice, setNotice] as const;
}

const QUEUED_TEXT = "Salvo no aparelho — será enviado quando a conexão voltar.";

/** Technician — execution of a work order (checklist, pause, labor, replacement, failure, completion). */
export default function WorkOrderExecPage() {
  const { id = "" } = useParams();
  const user = useAuthStore((s) => s.user);
  const q = useTechWorkOrder(id);
  const wo = useEffectiveWorkOrder(q.data?.data);
  const { queue } = useQueueState();
  const online = useOnline();
  const action = useTechAction();
  const names = useWoNames();
  const [sheet, setSheet] = useState<SheetKind>(null);
  const [notice, setNotice] = useNotice();
  const [checked, setChecked] = useState("");
  const today = isoDay();

  if (q.isPending) {
    return <LoadingRegion label="Carregando OS…"><div className="space-y-3"><Skeleton className="h-28 rounded-2xl" /><Skeleton className="h-64 rounded-2xl" /></div></LoadingRegion>;
  }
  if (q.isError || !wo) {
    return <ErrorState title="Não foi possível abrir a OS" error={q.error ?? new Error("OS não encontrada neste aparelho.")} onRetry={() => void q.refetch()} />;
  }

  const offered = offeredTransitions(wo, user);
  const pending = queue.filter((a) => a.wo_id === wo.id).length;
  const running = wo.state === "in_progress";
  const done = wo.task_list.filter((t) => t.done).length;

  async function run(a: Parameters<typeof action.mutateAsync>[0], ok: string) {
    try {
      const r = await action.mutateAsync(a);
      setNotice(r === "queued" ? { tone: "info", text: QUEUED_TEXT } : { tone: "ok", text: ok });
      return true;
    } catch (err) {
      setNotice({ tone: "danger", text: a.kind === "transition" ? transitionErrorMessage(err) : apiErrorMessage(err, "Não foi possível salvar.") });
      return false;
    }
  }

  const positionMatch = checked ? (checked === wo.position_id || checked === wo.machine_id ? "ok" : "mismatch") : null;

  // primary actions (thumb zone)
  const can = (to: WoState) => offered.includes(to);
  const startTo: WoState | null = can("in_progress") ? "in_progress" : null;
  const startLabel = wo.state === "open" ? "Assumir chamado" : wo.state === "on_hold" ? "Retomar" : "Iniciar";

  return (
    <div className="space-y-4">
      <Link to="/m/work-orders" className="inline-flex min-h-10 items-center gap-1 text-body font-medium text-ok">‹ Minhas OS</Link>

      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-title text-fg">{wo.number}</span>
          <WoStateBadge state={wo.state} />
          {q.data?.source === "cache" && <Badge tone="warn">Salva no aparelho {formatDateTime(q.data.saved_at)}</Badge>}
        </div>
        <h1 className="text-heading text-fg">{wo.title}</h1>
        <p className="text-body text-fg-muted">{names.machine(wo)}{wo.position_name ? ` · ${wo.position_name}` : ""}</p>
        <div className="flex flex-wrap gap-1.5">
          <WoTypeBadge type={wo.type} urgency={wo.urgency} />
          {isWoOverdue(wo, today) && <OverdueBadge />}
          {wo.stops_machine && <Badge tone="warn">Para a máquina</Badge>}
          {pending > 0 && <Badge tone="info" dot pulse>{pending} pendente{pending > 1 ? "s" : ""} de envio</Badge>}
        </div>
        {wo.state === "on_hold" && <p className="text-caption text-warn">Em espera: {holdReasonLabel(wo.hold_reason)}</p>}
        {wo.stops_machine && (wo.state === "scheduled" || wo.state === "open") && (
          <p className="rounded-xl bg-warn-solid/10 px-3 py-2 text-caption text-fg">Esta OS para a máquina: confirme com a produção que a máquina foi liberada antes de iniciar.</p>
        )}
      </header>

      <p aria-live="polite" data-testid="exec-notice"
        className={clsx("rounded-xl px-4 py-3 text-body font-medium", !notice && "sr-only",
          notice?.tone === "danger" ? "bg-danger-solid/10 text-danger" : notice?.tone === "info" ? "bg-info-solid/10 text-info" : "bg-ok-solid/15 text-ok")}>
        {notice?.text}
      </p>

      {wo.source_alert_id && (
        <Block title="Por que esta OS existe (preditiva)">
          <WoPredictiveBlock alertId={wo.source_alert_id} online={online} />
        </Block>
      )}

      {(wo.state === "scheduled" || wo.state === "open") && wo.position_id && (
        <Block title="Conferir posição">
          <ScanInput id="pos-check" label="QR da etiqueta da posição" value={checked} onChange={setChecked}
            hint="Leia a etiqueta na máquina para confirmar que está no lugar certo (opcional)." />
          {positionMatch === "ok" && <p className="text-body font-semibold text-ok">✓ Posição confere.</p>}
          {positionMatch === "mismatch" && <p role="alert" className="text-body font-semibold text-danger">Esta etiqueta não é da posição da OS. Confira a máquina.</p>}
        </Block>
      )}

      {wo.task_list.length > 0 && (
        <Block title={`Checklist ${done}/${wo.task_list.length}`}>
          {!running && <p className="text-caption text-fg-subtle">{wo.state === "completed" ? "OS concluída." : wo.state === "on_hold" ? "Retome a OS para marcar os itens." : "Inicie a OS para marcar os itens."}</p>}
          <Progress value={wo.task_list.length ? done / wo.task_list.length : 0} label="Itens do checklist feitos" size="sm" />
          <ul className="space-y-2">
            {wo.task_list.map((t) => (
              <li key={t.id} className="flex items-stretch gap-2">
                <button type="button" role="checkbox" aria-checked={t.done} disabled={!running || action.isPending} data-testid="task-item"
                  onClick={() => void run({ kind: "task", wo, body: { task_id: t.id, done: !t.done } }, t.done ? "Item desmarcado." : "Item feito.")}
                  className={clsx("flex min-h-14 flex-1 items-center gap-3 rounded-xl border-2 px-3 text-left text-body disabled:opacity-70",
                    t.done ? "border-ok-solid/60 bg-ok-solid/10" : "border-line")}>
                  <span aria-hidden="true" className={clsx("flex size-7 shrink-0 items-center justify-center rounded-lg border-2", t.done ? "border-ok-solid bg-ok-solid text-white" : "border-line-strong")}>
                    {t.done && <svg viewBox="0 0 20 20" fill="currentColor" className="size-5"><path fillRule="evenodd" d="M16.7 5.3a1 1 0 0 1 0 1.4l-8 8a1 1 0 0 1-1.4 0l-4-4a1 1 0 1 1 1.4-1.4L8 12.6l7.3-7.3a1 1 0 0 1 1.4 0z" clipRule="evenodd" /></svg>}
                  </span>
                  <span className={clsx("text-fg", t.done && "text-fg-muted")}>{t.text}</span>
                </button>
                {running && (
                  <button type="button" aria-label={`Problema no item: ${t.text}`} onClick={() => setSheet("problem")}
                    className="flex min-h-14 w-12 shrink-0 items-center justify-center rounded-xl border-2 border-line text-warn">!</button>
                )}
              </li>
            ))}
          </ul>
        </Block>
      )}

      <Block title="Horas" action={running ? <button type="button" onClick={() => setSheet("labor")} className="min-h-10 rounded-lg px-3 text-body font-semibold text-ok">Apontar horas</button> : undefined}>
        <div className="grid grid-cols-3 gap-2 text-center">
          <div><p className="text-caption text-fg-subtle">Apontadas</p><p className="text-heading tabular text-fg" data-testid="labor-hours">{hoursLabel(wo.labor_hours)}</p></div>
          <div><p className="text-caption text-fg-subtle">Estimadas</p><p className="text-heading tabular text-fg">{hoursLabel(wo.estimated_hours)}</p></div>
          <div><p className="text-caption text-fg-subtle">Restantes</p><p className="text-heading tabular text-fg">{hoursLabel(remainingHours(wo))}</p></div>
        </div>
      </Block>

      {(running || wo.replacements.length > 0) && (
        <Block title="Troca de componente" action={running && wo.position_id ? <button type="button" onClick={() => setSheet("replace")} className="min-h-10 rounded-lg px-3 text-body font-semibold text-ok">Registrar troca</button> : undefined}>
          {wo.replacements.length === 0 ? <p className="text-caption text-fg-subtle">{wo.position_id ? "Nenhuma troca registrada." : "OS da máquina inteira: escolha a posição na troca."}</p> : (
            <ul className="space-y-2">
              {wo.replacements.map((r, i) => (
                <li key={r.id ?? i} className="text-body text-fg">
                  {REMOVAL_REASON_LABEL[r.removal_reason as RemovalReason] ?? r.removal_reason}: {[r.new_component?.manufacturer, r.new_component?.model, r.new_component?.serial_number].filter(Boolean).join(" · ") || "novo componente"}
                </li>
              ))}
            </ul>
          )}
          {running && !wo.position_id && <button type="button" onClick={() => setSheet("replace")} className="min-h-12 w-full rounded-xl border-2 border-line text-body font-semibold text-fg">Registrar troca</button>}
        </Block>
      )}

      {(wo.type === "corrective" || wo.failure_id) && (
        <Block title="Dados da falha" action={<button type="button" onClick={() => setSheet("failure")} className="min-h-10 rounded-lg px-3 text-body font-semibold text-ok">{wo.failure_id ? "Informar" : "Ver"}</button>}>
          <p className="text-caption text-fg-subtle">
            {wo.failure_id ? "Informe posição, modo de falha (ISO 14224) e severidade. Mecanismo e causa podem ficar para o planejador." : "Esta OS ainda não tem registro de falha vinculado."}
          </p>
        </Block>
      )}

      {wo.state === "completed" && (
        <EmptyState title="OS concluída" description="Aguardando o supervisor verificar e fechar. Se a OS era de um plano, ele foi zerado e a próxima data aparece em Próximas manutenções." />
      )}

      <Block title="Histórico">
        <ol className="space-y-1.5 text-caption">
          {[...wo.history].reverse().slice(0, 6).map((h, i) => (
            <li key={`${h.at}-${i}`} className="text-fg-muted"><span className="font-medium text-fg">{WO_STATE_LABEL[h.to_state]}</span> · {formatDateTime(h.at)}{h.note ? ` — ${h.note}` : ""}</li>
          ))}
        </ol>
      </Block>

      {/* Thumb-zone action bar */}
      {(startTo || running) && (
        <div className="fixed inset-x-0 bottom-16 z-20 border-t border-line bg-surface/95 px-4 py-3 backdrop-blur" style={{ marginBottom: "env(safe-area-inset-bottom)" }}>
          <div className="mx-auto flex max-w-xl gap-2">
            {running ? (
              <>
                {can("on_hold") && <button type="button" onClick={() => setSheet("pause")} className="min-h-14 flex-1 rounded-xl border-2 border-line-strong text-heading font-semibold text-fg">Pausar</button>}
                {can("completed") && <button type="button" onClick={() => setSheet("complete")} className="min-h-14 flex-[2] rounded-xl bg-accent text-heading font-bold text-accent-fg">Concluir</button>}
              </>
            ) : startTo && (
              <button type="button" disabled={action.isPending}
                onClick={() => void run({ kind: "transition", wo, body: { to: "in_progress", note: positionMatch === "ok" ? "posição conferida por QR" : null } }, "OS iniciada.")}
                className={clsx("min-h-14 w-full rounded-xl text-heading font-bold text-white disabled:opacity-60", wo.state === "open" ? "bg-danger-solid" : "bg-accent")}>
                {startLabel}
              </button>
            )}
          </div>
        </div>
      )}

      <PauseSheet open={sheet === "pause"} wo={wo} onClose={() => setSheet(null)}
        onSubmit={(body) => void run({ kind: "transition", wo, body: { to: "on_hold", ...body } }, "OS pausada.").then((ok) => ok && setSheet(null))} />
      <CompleteSheet open={sheet === "complete"} wo={wo} online={online} onClose={() => setSheet(null)}
        onSubmit={async (hours, note, extra) => {
          if (hours > 0 && !(await run({ kind: "labor", wo, body: { hours, note: note || null } }, "Horas apontadas."))) return;
          if (await run({ kind: "transition", wo, body: { to: "completed", note: note || null, ...extra } }, "OS concluída.")) {
            setSheet(null);
          }
        }} />
      <LaborSheet open={sheet === "labor"} wo={wo} onClose={() => setSheet(null)}
        onSubmit={(hours, remaining, note) => void run({ kind: "labor", wo, body: { hours, remaining_hours: remaining, note: note || null } }, "Horas apontadas.").then((ok) => ok && setSheet(null))} />
      <ReplaceSheet open={sheet === "replace"} wo={wo} online={online} onClose={() => setSheet(null)} onDone={(t) => { setNotice({ tone: "ok", text: t }); setSheet(null); void q.refetch(); }} />
      <FailureSheet open={sheet === "failure"} wo={wo} online={online} onClose={() => setSheet(null)} onDone={(t) => { setNotice({ tone: "ok", text: t }); setSheet(null); }} />
      <ProblemSheet open={sheet === "problem"} wo={wo} online={online} onClose={() => setSheet(null)}
        onDone={(created) => { setNotice({ tone: "ok", text: `${created.number} aberta para o planejador aprovar.` }); setSheet(null); }}
        onOpenFailure={() => setSheet("failure")} />
    </div>
  );
}

// ── sheets ────────────────────────────────────────────────────────

function PauseSheet({ open, wo, onClose, onSubmit }: { open: boolean; wo: WorkOrder; onClose: () => void; onSubmit: (b: { hold_reason: string; remaining_hours: number; note: string | null }) => void }) {
  const [reason, setReason] = useState<string>("");
  const [remaining, setRemaining] = useState(remainingHours(wo));
  const [note, setNote] = useState("");
  useEffect(() => { if (open) { setReason(""); setRemaining(remainingHours(wo)); setNote(""); } }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Sheet open={open} onClose={onClose} title="Pausar OS" description="O tempo em espera não conta como horas trabalhadas."
      footer={<button type="button" disabled={!reason} onClick={() => onSubmit({ hold_reason: reason, remaining_hours: remaining, note: note.trim() || null })}
        className="min-h-14 w-full rounded-xl bg-accent text-heading font-bold text-accent-fg disabled:opacity-50">Pausar</button>}>
      <div className="space-y-5">
        <BigChoice name="hold" legend="Motivo" options={HOLD_REASONS} value={reason} onChange={setReason} />
        <HoursStepper id="pause-remaining" label="Horas restantes estimadas" value={remaining} onChange={setRemaining} />
        <Field label="Observação (opcional)" htmlFor="pause-note">
          <textarea id="pause-note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} className={inputClass} placeholder="Ex.: falta o kit de vedação 50 mm" />
        </Field>
      </div>
    </Sheet>
  );
}

const FINDINGS: { value: WoFinding; label: string }[] = [
  { value: "defect_confirmed", label: "Defeito confirmado (degradação encontrada)" },
  { value: "no_defect", label: "Sem defeito (componente em bom estado)" },
];
const DEFECT_SEVERITIES: { value: "incipient" | "degraded"; label: string }[] = [
  { value: "incipient", label: "Incipiente (início de desgaste)" },
  { value: "degraded", label: "Degradada (perda parcial de função)" },
];

type CompleteExtra = { finding?: WoFinding; resets_baseline?: boolean };

function CompleteSheet({ open, wo, online, onClose, onSubmit }: { open: boolean; wo: WorkOrder; online: boolean; onClose: () => void; onSubmit: (hours: number, note: string, extra: CompleteExtra) => Promise<void> }) {
  const [hours, setHours] = useState(0);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const predictive = !!wo.source_alert_id;
  const [finding, setFinding] = useState<WoFinding | "">("");
  const [mode, setMode] = useState("");
  const [sev, setSev] = useState<"incipient" | "degraded" | "">("");
  const [resets, setResets] = useState(true);
  const [err, setErr] = useState("");
  const alert = useAlert(predictive && open && online ? wo.source_alert_id ?? null : null);
  const modes = alert.data ? COMPATIBLE_MODES[alert.data.feature] : [];
  const since = useMemo(() => [...(wo.history ?? [])].reverse().find((h) => h.to_state === "in_progress")?.at ?? wo.started_at, [wo]);
  useEffect(() => {
    if (open) {
      setHours(elapsedSinceResume(wo, Date.now())); setNote(""); setFinding(wo.finding ?? ""); setMode(""); setSev(""); setErr("");
      setResets(wo.resets_baseline ?? (wo.type === "predictive" || wo.type === "corrective" || wo.replacements.length > 0));
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const undone = wo.task_list.filter((t) => !t.done).length;
  const showResets = !!wo.position_id && (wo.type === "predictive" || wo.type === "corrective" || wo.replacements.length > 0);

  async function submit() {
    setErr("");
    if (predictive && !finding) { setErr("Informe o achado da inspeção preditiva."); return; }
    if (predictive && finding === "defect_confirmed") {
      if (!online) { setErr("Registrar o defeito exige conexão."); return; }
      if (!mode || !sev) { setErr("Informe o modo de falha e a severidade do defeito."); return; }
    }
    setBusy(true);
    try {
      if (predictive && finding === "defect_confirmed" && wo.machine_id) {
        try {
          await failuresApi.create({
            machine_id: wo.machine_id, position_id: wo.position_id, failed_at: new Date().toISOString(), severity: sev,
            failure_mode: mode, detection_method: "continuous_condition_monitoring", work_order_id: wo.id,
            note: note.trim() || "Defeito confirmado na OS preditiva",
          });
        } catch (e) {
          setErr(apiErrorMessage(e, "Não foi possível registrar o defeito."));
          return;
        }
      }
      const extra: CompleteExtra = {};
      if (predictive && finding) extra.finding = finding;
      if (showResets) extra.resets_baseline = resets;
      await onSubmit(hours, note.trim(), extra);
    } finally { setBusy(false); }
  }

  return (
    <Sheet open={open} onClose={onClose} title="Concluir OS"
      footer={<button type="button" disabled={busy} onClick={() => void submit()}
        className="min-h-14 w-full rounded-xl bg-accent text-heading font-bold text-accent-fg disabled:opacity-50">Concluir OS</button>}>
      <div className="space-y-5">
        {undone > 0 && <p role="alert" className="rounded-xl bg-warn-solid/10 px-3 py-2 text-body text-warn">{undone} item(ns) do checklist não marcado(s).</p>}
        {predictive && (
          <div className="space-y-4" data-testid="complete-finding">
            <BigChoice name="finding" legend="Achado da inspeção preditiva (obrigatório)" options={FINDINGS} value={finding} onChange={setFinding} />
            {finding === "defect_confirmed" && (
              <>
                <Field label="Modo de falha (ISO 14224)" htmlFor="pred-mode" hint="Compatível com o sinal que gerou o alerta.">
                  <select id="pred-mode" value={mode} onChange={(e) => setMode(e.target.value)} className={inputClass} disabled={!modes.length}>
                    <option value="">{modes.length ? "Escolha…" : "Carregando…"}</option>
                    {modes.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </Field>
                <BigChoice name="defect-sev" legend="Severidade do defeito" options={DEFECT_SEVERITIES} value={sev} onChange={setSev} />
                <p className="text-caption text-fg-subtle">Registra a falha (detectada por monitoramento de condição). Falha incipiente não entra no MTBF.</p>
              </>
            )}
          </div>
        )}
        {showResets && (
          <label className="flex min-h-12 items-center gap-3 rounded-xl border-2 border-line px-3 text-body text-fg">
            <input type="checkbox" checked={resets} onChange={(e) => setResets(e.target.checked)} className="size-5" />
            A condição do componente mudou (troca/reparo): reiniciar a referência da preditiva
          </label>
        )}
        {err && <p role="alert" className="rounded-xl bg-danger-solid/10 px-3 py-2 text-body text-danger">{err}</p>}
        <HoursStepper id="complete-hours" label="Horas trabalhadas nesta sessão" value={hours} onChange={setHours} />
        <p className="text-caption text-fg-subtle">Pré-preenchido pelo tempo em execução{since ? ` desde ${formatDateTime(since)}` : ""}. Já apontadas: {hoursLabel(wo.labor_hours)}. Ajuste se necessário.</p>
        <Field label="Observação (opcional)" htmlFor="complete-note">
          <textarea id="complete-note" rows={3} value={note} onChange={(e) => setNote(e.target.value)} className={inputClass} />
        </Field>
      </div>
    </Sheet>
  );
}

function LaborSheet({ open, wo, onClose, onSubmit }: { open: boolean; wo: WorkOrder; onClose: () => void; onSubmit: (h: number, remaining: number, note: string) => void }) {
  const [hours, setHours] = useState(0.5);
  const [remaining, setRemaining] = useState(remainingHours(wo));
  const [note, setNote] = useState("");
  useEffect(() => { if (open) { setHours(0.5); setRemaining(Math.max(0, remainingHours(wo) - 0.5)); setNote(""); } }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Sheet open={open} onClose={onClose} title="Apontar horas"
      footer={<button type="button" disabled={hours <= 0} onClick={() => onSubmit(hours, remaining, note.trim())}
        className="min-h-14 w-full rounded-xl bg-accent text-heading font-bold text-accent-fg disabled:opacity-50">Apontar</button>}>
      <div className="space-y-5">
        <HoursStepper id="labor-hours" label="Horas trabalhadas" value={hours} onChange={setHours} />
        <HoursStepper id="labor-remaining" label="Horas restantes" value={remaining} onChange={setRemaining} />
        <Field label="Observação (opcional)" htmlFor="labor-note">
          <textarea id="labor-note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} className={inputClass} />
        </Field>
      </div>
    </Sheet>
  );
}

const REMOVAL_OPTIONS = (Object.keys(REMOVAL_REASON_LABEL) as RemovalReason[]).map((r) => ({ value: r, label: REMOVAL_REASON_LABEL[r] }));

function OnlineOnly({ online }: { online: boolean }) {
  return online ? null : <p role="alert" className="rounded-xl bg-warn-solid/10 px-3 py-2 text-body text-warn">Precisa de conexão. Tente de novo quando o sinal voltar.</p>;
}

function ReplaceSheet({ open, wo, online, onClose, onDone }: { open: boolean; wo: WorkOrder; online: boolean; onClose: () => void; onDone: (t: string) => void }) {
  const [positionId, setPositionId] = useState(wo.position_id ?? "");
  const position = usePosition(open && positionId ? positionId : null);
  const replacement = useReplacement();
  const [reason, setReason] = useState<RemovalReason | "">("");
  const [serial, setSerial] = useState("");
  const [manufacturer, setManufacturer] = useState("");
  const [model, setModel] = useState("");
  const [lookup, setLookup] = useState<{ id: string; label: string } | null | "none">(null);
  const current = position.data?.installed_component;

  useEffect(() => {
    if (!open) return;
    setPositionId(wo.position_id ?? "");
    setReason(wo.type === "corrective" ? "failure" : wo.type === "preventive" ? "preventive_replacement" : "");
    setSerial(""); setLookup(null); replacement.reset();
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (current) { setManufacturer((m) => m || current.manufacturer || ""); setModel((m) => m || current.model || ""); }
  }, [current]);

  async function find() {
    if (!serial.trim()) return;
    try {
      const r = await componentsApi.list({ in_stock: true });
      const hit = r.data.find((c) => (c.serial_number ?? "").toUpperCase() === serial.trim().toUpperCase());
      setLookup(hit ? { id: hit.id, label: [hit.manufacturer, hit.model].filter(Boolean).join(" ") || "Componente do estoque" } : "none");
      if (hit) { setManufacturer(hit.manufacturer ?? ""); setModel(hit.model ?? ""); }
    } catch {
      setLookup("none");
    }
  }

  const invalid = !online || !positionId || !reason || !serial.trim() || (lookup !== null && lookup !== "none" ? false : !manufacturer.trim());
  function submit() {
    if (invalid || !reason) return;
    const stock = lookup && lookup !== "none" ? lookup : null;
    replacement.mutate({
      id: wo.id,
      body: stock
        ? { position_id: positionId, new_component_id: stock.id, removal_reason: reason }
        : { position_id: positionId, removal_reason: reason, new_component: { manufacturer: manufacturer.trim() || null, model: model.trim() || null, serial_number: serial.trim(), component_type: position.data?.component_type ?? null } },
    }, { onSuccess: () => onDone("Troca registrada. Ao concluir a OS, o componente antigo sai com a idade atual e o novo começa do zero.") });
  }

  return (
    <Sheet open={open} onClose={onClose} title="Trocar componente" description="Vale ao concluir a OS (zera os planos da posição com “zerar na troca”)."
      footer={
        <div className="space-y-2">
          {replacement.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(replacement.error, "Não foi possível registrar a troca.")}</p>}
          <button type="button" disabled={invalid || replacement.isPending} onClick={submit}
            className="min-h-14 w-full rounded-xl bg-accent text-heading font-bold text-accent-fg disabled:opacity-50">Registrar troca</button>
        </div>
      }>
      <div className="space-y-5">
        <OnlineOnly online={online} />
        {!wo.position_id && (
          <Field label="ID da posição (QR da etiqueta)" htmlFor="rep-pos">
            <input id="rep-pos" value={positionId} onChange={(e) => setPositionId(e.target.value.trim())} className={clsx(inputClass, "min-h-14 font-mono")} />
          </Field>
        )}
        <div className="rounded-xl border border-line bg-surface-2/40 p-3">
          <p className="text-caption text-fg-subtle">Sai (instalado agora)</p>
          {position.isPending && positionId ? <Skeleton className="mt-1 h-5 w-48" /> : current ? (
            <p className="text-body font-medium text-fg" data-testid="current-component">
              {[current.manufacturer, current.model].filter(Boolean).join(" ") || "Componente"}{current.serial_number ? ` · série ${current.serial_number}` : ""}
              {position.data?.component_usage && <span className="block text-caption text-fg-subtle">Idade: {formatInt(position.data.component_usage.cycles)} ciclos · {formatInt(position.data.component_usage.hours)} h</span>}
            </p>
          ) : <p className="text-body text-fg-muted">Nenhum componente instalado nesta posição.</p>}
        </div>
        <BigChoice name="removal" legend="Motivo da remoção" options={REMOVAL_OPTIONS} value={reason} onChange={setReason} />
        <ScanInput id="rep-serial" label="Série do componente novo" value={serial} onChange={(v) => { setSerial(v); setLookup(null); }} onSubmit={() => void find()}
          placeholder="Ex.: SMC-A1-0003" hint="Leia o código da etiqueta ou digite e toque Enter para buscar no estoque." />
        {serial && lookup === null && <button type="button" onClick={() => void find()} className="min-h-12 w-full rounded-xl border-2 border-line text-body font-semibold text-fg">Buscar no estoque</button>}
        {lookup && lookup !== "none" && <p className="text-body font-semibold text-ok">✓ Encontrado no estoque: {lookup.label}</p>}
        {(lookup === "none" || lookup === null) && (
          <div className="grid gap-3">
            {lookup === "none" && <p className="text-caption text-fg-subtle">Não está no estoque: informe fabricante e modelo para cadastrar.</p>}
            <Field label="Fabricante" htmlFor="rep-man"><input id="rep-man" value={manufacturer} onChange={(e) => setManufacturer(e.target.value)} className={clsx(inputClass, "min-h-12")} /></Field>
            <Field label="Modelo" htmlFor="rep-model"><input id="rep-model" value={model} onChange={(e) => setModel(e.target.value)} className={clsx(inputClass, "min-h-12")} /></Field>
          </div>
        )}
      </div>
    </Sheet>
  );
}

function FailureSheet({ open, wo, online, onClose, onDone }: { open: boolean; wo: WorkOrder; online: boolean; onClose: () => void; onDone: (t: string) => void }) {
  const f = useFailure(open ? wo.failure_id : null);
  const patch = usePatchFailure();
  return (
    <Sheet open={open} onClose={onClose} title="Dados da falha" description="O restabelecimento é medido pela máquina (não precisa digitar).">
      <div className="space-y-4">
        <OnlineOnly online={online} />
        {!wo.failure_id ? <p className="text-body text-fg-muted">Esta OS não tem falha vinculada.</p>
          : f.isPending ? <Skeleton className="h-64" />
          : f.isError || !f.data ? <ErrorState title="Não foi possível carregar a falha" error={f.error} onRetry={() => void f.refetch()} />
          : (
            <FailureForm failure={f.data} idPrefix="mf" busy={patch.isPending} submitLabel="Salvar dados da falha"
              error={patch.isError ? apiErrorMessage(patch.error, "Não foi possível salvar.") : null}
              onSave={(body) => online && patch.mutate({ id: f.data.id, body }, { onSuccess: () => onDone("Dados da falha salvos.") })} />
          )}
      </div>
    </Sheet>
  );
}

function ProblemSheet({ open, wo, online, onClose, onDone, onOpenFailure }: {
  open: boolean; wo: WorkOrder; online: boolean; onClose: () => void; onDone: (w: WorkOrder) => void; onOpenFailure: () => void;
}) {
  const create = useCreateWorkOrder();
  const qc = useQueryClient();
  const found = useMutation({
    mutationFn: (severity: FailureSeverity) => failuresApi.create({
      machine_id: wo.machine_id as string, position_id: wo.position_id, failed_at: new Date().toISOString(), severity,
      detection_method: "periodic_maintenance", work_order_id: wo.id, note: text.trim() || null,
    }).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["maintenance"] }),
  });
  const [text, setText] = useState("");
  const [severity, setSeverity] = useState<FailureSeverity>("degraded");
  useEffect(() => { if (open) { setText(""); setSeverity("degraded"); create.reset(); found.reset(); } }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Sheet open={open} onClose={onClose} title="Item não ok" description={`${wo.number} · ${wo.title}`}>
      <div className="space-y-4">
        <OnlineOnly online={online} />
        <Field label="O que encontrou?" htmlFor="prob-text">
          <textarea id="prob-text" rows={3} value={text} onChange={(e) => setText(e.target.value)} className={inputClass} placeholder="Ex.: haste riscada, vazamento leve" />
        </Field>
        {create.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(create.error, "Não foi possível abrir a OS.")}</p>}
        <button type="button" disabled={!online || text.trim().length < 3 || create.isPending}
          onClick={() => wo.machine_id && create.mutate({
            type: "corrective", urgency: "deferred", machine_id: wo.machine_id, position_id: wo.position_id,
            title: `Corretiva programada: ${text.trim().slice(0, 80)}`, description: `Encontrado na ${wo.number}: ${text.trim()}`,
          }, { onSuccess: onDone })}
          className="min-h-14 w-full rounded-xl bg-accent text-heading font-bold text-accent-fg disabled:opacity-50">Abrir corretiva programada</button>
        <div className="space-y-3 rounded-xl border border-line p-3">
          <p className="text-body font-medium text-fg">Ou registrar falha encontrada (sem parada)</p>
          <BigChoice name="found-sev" legend="Severidade" value={severity} onChange={setSeverity}
            options={[{ value: "degraded", label: "Degradada — função parcial" }, { value: "incipient", label: "Incipiente — ainda funciona" }, { value: "critical", label: "Crítica — perdeu a função" }] as const} />
          {found.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(found.error, "Não foi possível registrar a falha.")}</p>}
          {found.isSuccess ? <p className="text-body font-semibold text-ok">Falha registrada (detecção: manutenção periódica). O planejador completa a análise.</p> : (
            <button type="button" disabled={!online || !wo.machine_id || found.isPending} onClick={() => found.mutate(severity)}
              className="min-h-14 w-full rounded-xl border-2 border-line-strong text-heading font-semibold text-fg disabled:opacity-50">Registrar falha encontrada</button>
          )}
          <p className="text-caption text-fg-subtle">Conta no MTBF do componente, não no MTTR (não parou a produção).</p>
        </div>
        {wo.failure_id && (
          <button type="button" onClick={onOpenFailure} className="min-h-14 w-full rounded-xl border-2 border-line text-heading font-semibold text-fg">Informar dados da falha da OS</button>
        )}
        <p className="text-caption text-fg-subtle">Defeito sem perda de função vira pedido para o planejador (corretiva programada).</p>
      </div>
    </Sheet>
  );
}

