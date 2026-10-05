import { useState } from "react";
import clsx from "clsx";
import { useEmergencyCall } from "@/hooks/useMaintenance";
import { useAuthStore } from "@/store/auth";
import type { DowntimeEvent } from "@/services/types";
import { apiErrorMessage } from "@/utils/errors";
import { can } from "@/utils/maintenance";

/**
 * "Chamar manutenção" from a stop (supervisor / operator, workflows §4): creates the emergency corrective
 * work order linked to the downtime event (the server links the draft failure and notifies the on-call
 * technicians). The stop keeps counting as EQP-* until the machine runs again.
 */
export default function CallMaintenanceButton({ ev, className, size = "lg" }: { ev: Pick<DowntimeEvent, "id" | "machine_id" | "machine_name" | "fault_code" | "suggested_reason_code" | "reason_code">; className?: string; size?: "md" | "lg" }) {
  const user = useAuthStore((s) => s.user);
  const create = useEmergencyCall();
  const [done, setDone] = useState<string | null>(null);
  if (!can(user, "maintenance.request")) return null;

  function call() {
    const code = ev.reason_code ?? ev.suggested_reason_code;
    create.mutate({
      downtime_event_id: ev.id,
      machine_id: ev.machine_id,
      title: `Chamado: ${ev.machine_name ?? "máquina"} parada${ev.fault_code != null ? ` (falha ${ev.fault_code})` : ""}`,
      note: `Aberto a partir da parada${code ? ` ${code}` : ""}.`,
    }, { onSuccess: (wo) => setDone(`Manutenção chamada: ${wo.number}. Os técnicos de plantão foram notificados.`) });
  }

  if (done) {
    return <p role="status" className={clsx("rounded-xl bg-ok-solid/15 px-4 py-3 text-body font-medium text-ok", className)}>{done}</p>;
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
