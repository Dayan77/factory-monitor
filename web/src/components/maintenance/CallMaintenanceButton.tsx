import { useState } from "react";
import clsx from "clsx";
import { useEmergencyCall } from "@/hooks/useMaintenance";
import { useAuthStore } from "@/store/auth";
import type { DowntimeEvent } from "@/services/types";
import type { EmergencyCallResponse } from "@/services/maintenanceTypes";
import { apiErrorMessage } from "@/utils/errors";
import { can } from "@/utils/maintenance";

/**
 * "Chamar manutenção" from a stop (supervisor / operator, workflows §4): creates the emergency corrective
 * work order linked to the downtime event (the server links the draft failure and notifies the on-call
 * technicians; without on-call coverage it escalates to planning/management). The stop keeps counting as EQP-*
 * until the machine runs again. The result tells the truth about who was reached: success only when an on-call
 * technician was notified, a warning when the call went to the fallback, an alert when nobody was reached.
 */

type Outcome = { tone: "ok" | "warn" | "danger"; text: string };

function callOutcome(wo: EmergencyCallResponse): Outcome {
  if (wo.already_open) return { tone: "ok", text: `Já existe chamado aberto para esta parada: ${wo.number}.` };
  const r = wo.emergency_call;
  if (!r) return { tone: "warn", text: `Chamado ${wo.number} aberto, mas não foi possível confirmar quem foi notificado. Avise por rádio/telefone.` };
  if (r.notified_count === 0) {
    return { tone: "danger", text: `Chamado ${wo.number} aberto, mas ninguém foi notificado: não há ninguém de plantão nesta linha. Avise por rádio/telefone.` };
  }
  if (r.on_call_technicians === 0) {
    // only planners / managers / admins count as "planejamento/gestão" — the line supervisor is not maintenance
    const e = r.escalated_count ?? 0;
    if (e === 0) {
      return { tone: "danger", text: `Chamado ${wo.number} aberto, mas nenhuma pessoa da manutenção foi notificada: não há técnico de plantão nesta linha nem planejamento/gestão para acionar (só a supervisão da linha recebeu). Avise a manutenção por rádio/telefone.` };
    }
    return { tone: "warn", text: `Chamado ${wo.number} aberto. Ninguém de plantão nesta linha: o planejamento/gestão foi avisado (${e} pessoa${e > 1 ? "s" : ""}) para acionar um técnico.` };
  }
  const n = r.on_call_technicians;
  return { tone: "ok", text: `Manutenção chamada: ${wo.number}. ${n} técnico${n > 1 ? "s" : ""} de plantão notificado${n > 1 ? "s" : ""}.` };
}

const TONE = {
  ok: "bg-ok-solid/15 text-ok",
  warn: "bg-warn-solid/15 text-warn",
  danger: "bg-danger-solid/15 text-danger",
} as const;
export default function CallMaintenanceButton({ ev, className, size = "lg" }: { ev: Pick<DowntimeEvent, "id" | "machine_id" | "machine_name" | "fault_code" | "suggested_reason_code" | "reason_code">; className?: string; size?: "md" | "lg" }) {
  const user = useAuthStore((s) => s.user);
  const create = useEmergencyCall();
  const [done, setDone] = useState<Outcome | null>(null);
  if (!can(user, "maintenance.request")) return null;

  function call() {
    const code = ev.reason_code ?? ev.suggested_reason_code;
    create.mutate({
      downtime_event_id: ev.id,
      machine_id: ev.machine_id,
      title: `Chamado: ${ev.machine_name ?? "máquina"} parada${ev.fault_code != null ? ` (falha ${ev.fault_code})` : ""}`,
      note: `Aberto a partir da parada${code ? ` ${code}` : ""}.`,
    }, { onSuccess: (wo) => setDone(callOutcome(wo)) });
  }

  if (done) {
    return (
      <p role={done.tone === "ok" ? "status" : "alert"} data-testid="call-maintenance-result" data-tone={done.tone}
        className={clsx("rounded-xl px-4 py-3 text-body font-medium", TONE[done.tone], className)}>{done.text}</p>
    );
  }
  return (
    <div className={className}>
      <button type="button" onClick={call} disabled={create.isPending} data-testid="call-maintenance"
        className={clsx("flex w-full items-center justify-center gap-2 rounded-xl border-2 border-danger-solid/60 bg-danger-solid/10 px-4 font-semibold text-danger transition-colors hover:bg-danger-solid/15 disabled:opacity-60",
          size === "lg" ? "min-h-14 text-heading" : "min-h-11 text-body")}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="size-5" aria-hidden="true">
          <path d="M14.7 6.3a4 4 0 0 0-5.4 5.2L3 17.8V21h3.2l6.3-6.3a4 4 0 0 0 5.2-5.4l-2.6 2.6-2.4-.6-.6-2.4z" />
        </svg>
        {create.isPending ? "Chamando…" : "Chamar manutenção"}
      </button>
      {create.isError && (
        <p role="alert" className="mt-1 text-caption text-danger">
          {apiErrorMessage(create.error, "Não foi possível chamar a manutenção. Tente de novo ou avise por rádio.")}
        </p>
      )}
    </div>
  );
}
