/** Texts of the map KPIs that must never go out without their warning (sprint6 §1.2/§1.4, kpis.md S3-2). */
import type { MachineKpis, MarkerBadge } from "@/services/sprint6Types";
import { formatPct } from "@/utils/format";

export const SUSPECT_TITLE = "Desempenho acima de 100 %: o tempo de ciclo ideal cadastrado provavelmente está maior que o real (ou faltam dados no turno). Revise em Configurações → Produtos.";

/** "OEE 196,2 %" never goes out alone: with P > 1 (S3-2) the value carries a visible warning sign + title. */
export function oeeText(k: MachineKpis | null | undefined): { text: string; suspect: boolean } {
  if (k?.oee == null) return { text: "OEE —", suspect: false };
  return { text: `OEE ${formatPct(k.oee)}`, suspect: !!k.ideal_cycle_time_suspect || k.oee > 1 };
}

/** Short texts of the extra badges (§1.2: predictive, data coverage < 0,95, parts outside the calendar). */
export function badgeText(b: MarkerBadge): string | null {
  if (b.kind === "data_coverage") return `dados incompletos (${formatPct(b.value ?? 0)} do turno com dados)`;
  if (b.kind === "outside_calendar") return `${b.parts ?? 0} peças fora do calendário (${b.window_h ?? 12} h)`;
  return null;
}

