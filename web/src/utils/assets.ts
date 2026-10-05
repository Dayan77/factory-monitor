import type { ComponentType, LifeMeasure, RemovalReason, UsageMeasure } from "@/services/types";

export const COMPONENT_TYPE_LABEL: Record<ComponentType, string> = {
  pneumatic_cylinder: "Cilindro pneumático",
  electric_motor: "Motor elétrico",
  servo: "Servomotor",
  robot_axis: "Eixo de robô",
  valve: "Válvula",
  gripper: "Garra",
  bearing: "Rolamento",
  belt: "Correia",
  pump: "Bomba",
  spindle: "Fuso / spindle",
  other: "Outro",
};

export function componentTypeLabel(t: string | null | undefined): string {
  return t ? COMPONENT_TYPE_LABEL[t as ComponentType] ?? t : "—";
}

export const REMOVAL_REASON_LABEL: Record<RemovalReason, string> = {
  failure: "Falha",
  preventive_replacement: "Troca preventiva",
  upgrade: "Melhoria / upgrade",
  other: "Outro",
};

export const MEASURE_LABEL: Record<UsageMeasure, string> = {
  cycles: "Ciclos",
  hours: "Horas",
  starts: "Partidas",
  good: "Peças boas",
  scrap: "Refugo",
  total: "Peças (total)",
};

export const LIFE_MEASURE_LABEL: Record<LifeMeasure, string> = {
  cycles: "ciclos",
  hours: "horas de operação",
  starts: "partidas",
};

const RUL_REASON_LABEL: Record<string, string> = {
  no_nominal_life: "sem vida nominal cadastrada",
  no_recent_usage: "sem uso nos últimos 14 dias",
  insufficient_data: "histórico insuficiente (mín. 3 dias)",
  beyond_horizon: "ritmo recente projeta além de 10 anos",
};

export function rulReasonLabel(reason: string | null | undefined): string {
  return reason ? RUL_REASON_LABEL[reason] ?? reason : "sem projeção";
}

/** Roles allowed to install/remove/register components (server: require_roles(admin)). */
export function canManageAssets(role: string | undefined): boolean {
  return role === "admin" || role === "superadmin";
}

/** "Fabricante Modelo" or a fallback. */
export function componentName(c: { manufacturer: string | null; model: string | null } | null | undefined): string {
  if (!c) return "—";
  const name = [c.manufacturer, c.model].filter(Boolean).join(" ");
  return name || "Componente sem identificação";
}
