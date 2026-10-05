import { isAxiosError } from "axios";
import type { WoState } from "@/services/maintenanceTypes";
import { apiErrorMessage } from "@/utils/errors";
import { WO_STATE_LABEL } from "@/utils/maintenance";

/** Allowed transitions carried by a 422 from POST /work-orders/{id}/transition (any of the usual shapes). */
export function allowedFromError(err: unknown): WoState[] | null {
  if (!isAxiosError(err) || err.response?.status !== 422) return null;
  const data: unknown = err.response.data;
  const pick = (o: unknown): unknown => {
    if (typeof o !== "object" || o === null) return undefined;
    const r = o as Record<string, unknown>;
    return r.allowed_transitions ?? r.allowed ?? (typeof r.detail === "object" ? pick(r.detail) : undefined);
  };
  const list = pick(data);
  return Array.isArray(list) ? list.filter((s): s is WoState => typeof s === "string") : null;
}

export function transitionErrorMessage(err: unknown): string {
  const allowed = allowedFromError(err);
  if (allowed) {
    const names = allowed.map((s) => WO_STATE_LABEL[s] ?? s).join(", ");
    return `Transição não permitida no estado atual.${names ? ` Permitidas: ${names}.` : " Nenhuma transição disponível."}`;
  }
  return apiErrorMessage(err, "Não foi possível mudar o estado da OS.");
}
