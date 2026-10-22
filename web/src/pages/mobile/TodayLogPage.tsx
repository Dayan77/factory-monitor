import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import EmptyState from "@/components/ui/EmptyState";
import ErrorState from "@/components/ui/ErrorState";
import Sheet from "@/components/ui/Sheet";
import { Field, inputClass } from "@/components/ui/Field";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { WoStateBadge } from "@/components/maintenance/badges";
import { HoursStepper } from "@/components/mobile/inputs";
import { transitionErrorMessage } from "@/components/maintenance/transitionError";
import { useMyWorkOrders, useQueueState, useTechAction } from "@/hooks/useTechnician";
import { useAuthStore } from "@/store/auth";
import { applyPending } from "@/services/offline";
import type { WorkOrder } from "@/services/maintenanceTypes";
import { formatDateTime } from "@/utils/format";
import { dayOf, hoursLabel, isoDay, remainingHours, WO_STATE_LABEL } from "@/utils/maintenance";

/** Technician — "Apontamentos de hoje" (workflows §3, fim do turno). */
export default function TodayLogPage() {
  const user = useAuthStore((s) => s.user);
  const mine = useMyWorkOrders(user?.id);
  const { queue } = useQueueState();
  const action = useTechAction();
  const today = isoDay();
  const [handover, setHandover] = useState<WorkOrder | null>(null);
  const [remaining, setRemaining] = useState(0);
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  const rows = useMemo(() => (mine.data?.data ?? []).map((w) => applyPending(w, queue)).filter((w) =>
    w.state === "in_progress" || (w.history ?? []).some((h) => dayOf(h.at) === today && (h.by === user?.id || h.by == null))), [mine.data, queue, today, user?.id]);
  const total = rows.reduce((s, w) => s + (w.labor_hours ?? 0), 0);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-title text-fg">Apontamentos de hoje</h1>
        <p className="text-caption text-fg-subtle">O que você iniciou, pausou ou concluiu hoje. OS em execução pedem pausa ou passagem de turno.</p>
      </div>
      <p aria-live="polite" className={msg ? "rounded-xl bg-ok-solid/15 px-4 py-3 text-body font-medium text-ok" : "sr-only"}>{msg}</p>
      {mine.isPending ? <LoadingRegion><Skeleton className="h-40 rounded-2xl" /></LoadingRegion>
        : mine.isError ? <ErrorState error={mine.error} onRetry={() => void mine.refetch()} />
        : rows.length === 0 ? <EmptyState title="Nada apontado hoje" description="Inicie uma OS em Minhas OS." />
        : (
          <>
            <p className="text-body text-fg">Total apontado nestas OS: <strong className="tabular">{hoursLabel(total)}</strong></p>
            <ul className="space-y-3">
              {rows.map((w) => (
                <li key={w.id} className="rounded-2xl border border-line bg-surface p-4 shadow-card">
                  <div className="flex items-center justify-between gap-2">
                    <Link to={`/m/work-orders/${w.id}`} className="text-heading text-ok">{w.number}</Link>
                    <WoStateBadge state={w.state} />
                  </div>
                  <p className="text-body text-fg">{w.title}</p>
                  <p className="text-caption text-fg-subtle">{hoursLabel(w.labor_hours)} apontadas · {hoursLabel(remainingHours(w))} restantes</p>
                  <ul className="mt-2 space-y-0.5 text-caption text-fg-muted">
                    {(w.history ?? []).filter((h) => dayOf(h.at) === today).map((h, i) => (
                      <li key={i}>{formatDateTime(h.at)} · {WO_STATE_LABEL[h.to_state]}{h.note ? ` — ${h.note}` : ""}</li>
                    ))}
                  </ul>
                  {w.state === "in_progress" && (
                    <div className="mt-3 grid grid-cols-2 gap-2">
                      <Link to={`/m/work-orders/${w.id}`} className="flex min-h-12 items-center justify-center rounded-xl border-2 border-line-strong text-body font-semibold text-fg">Pausar / concluir</Link>
                      <button type="button" onClick={() => { setHandover(w); setRemaining(remainingHours(w)); setNote(""); }}
                        className="min-h-12 rounded-xl border-2 border-line-strong text-body font-semibold text-fg">Passar o turno</button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </>
        )}
      <Sheet open={!!handover} onClose={() => setHandover(null)} title="Passar para o próximo turno" description="A OS fica em espera com a sua nota para o colega retomar."
        footer={<button type="button" disabled={!note.trim() || action.isPending}
          onClick={() => handover && action.mutate(
            { kind: "transition", wo: handover, body: { to: "on_hold", hold_reason: "shift_handover", remaining_hours: remaining, note: `Passagem de turno: ${note.trim()}` } },
            { onSuccess: (r) => { setMsg(r === "queued" ? "Salvo no aparelho — enviado quando a conexão voltar." : `${handover.number} passada para o próximo turno.`); setHandover(null); },
              onError: (e) => setMsg(transitionErrorMessage(e)) })}
          className="min-h-14 w-full rounded-xl bg-accent text-heading font-bold text-accent-fg disabled:opacity-50">Passar o turno</button>}>
        <div className="space-y-4">
          <HoursStepper id="ho-remaining" label="Horas restantes" value={remaining} onChange={setRemaining} />
          <Field label="Nota para o colega" htmlFor="ho-note">
            <textarea id="ho-note" rows={3} value={note} onChange={(e) => setNote(e.target.value)} className={inputClass} placeholder="Ex.: vedações trocadas, falta testar o curso" />
          </Field>
        </div>
      </Sheet>
    </div>
  );
}
