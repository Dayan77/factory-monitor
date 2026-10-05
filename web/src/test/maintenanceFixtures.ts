import type { FailureRecord, PlanStatus, WorkOrder } from "@/services/maintenanceTypes";
import type { AuthUser } from "@/store/auth";

export function user(over: Partial<AuthUser> = {}): AuthUser {
  return { id: "u-plan", name: "Paula Planejadora", email: "p@example.com", role: "maintenance_planner", roles: ["maintenance_planner"], tenant_id: "t1", ...over };
}

export const TECH = user({ id: "u-tech", name: "Tiago Técnico", role: "technician", roles: ["technician"] });
export const SUP = user({ id: "u-sup", name: "Sílvia", role: "production_supervisor", roles: ["production_supervisor"] });
export const OPERATOR = user({ id: "u-op", name: "Otto", role: "operator", roles: ["operator"] });

export function workOrder(over: Partial<WorkOrder> = {}): WorkOrder {
  return {
    id: "wo-1", number: "OS-114", type: "preventive", urgency: null, state: "scheduled", plan_id: "PL2", position_id: "POS-A2",
    machine_id: "m-1", downtime_event_id: null, failure_id: null, title: "Troca de vedações — Cilindro A2", description: null,
    task_list: [{ id: "t1", text: "Despressurizar", done: false }, { id: "t2", text: "Trocar kit", done: false }],
    estimated_hours: 1.5, labor_hours: 0, remaining_hours: null, stops_machine: true, scheduled_for: "2026-12-03",
    assignee_id: "u-tech", created_at: "2026-11-20T12:00:00Z", requested_at: null, approved_at: null, started_at: null,
    completed_at: null, closed_at: null, cancelled_at: null, cancel_reason: null, hold_reason: null, replacements: [],
    history: [
      { at: "2026-11-20T12:00:00Z", by: null, from_state: null, to_state: "draft", note: null },
      { at: "2026-11-27T12:00:00Z", by: "u-plan", by_name: "Paula Planejadora", from_state: "approved", to_state: "scheduled", note: "semana 49" },
    ],
    allowed_transitions: ["in_progress", "cancelled"],
    machine_name: "Estação de Fixação", position_name: "Cilindro A2",
    ...over,
  };
}

export function planStatus(over: Partial<PlanStatus> = {}): PlanStatus {
  return {
    plan_id: "PL3", plan_name: "Lubrificação dos rolamentos", position_id: "POS-MOT", position_name: "Motor do transportador",
    machine_id: "m-3", machine_name: "Transportador de Saída", state: "due_soon", consumed: 0.82, determining_trigger: "starts",
    triggers: [
      { measure: "hours", interval: 4000, usage: 3200, consumed: 0.8, projected_date: "2027-01-10" },
      { measure: "starts", interval: 30000, usage: 24600, consumed: 0.82, projected_date: "2027-01-15" },
      { measure: "days", interval: 365, usage: 225, consumed: 0.6164, projected_date: "2027-04-20" },
    ],
    projected_date: "2027-01-10", next_due_usage: { measure: "hours", value: 45800 }, open_work_order_id: null,
    ...over,
  };
}

export function failure(over: Partial<FailureRecord> = {}): FailureRecord {
  return {
    id: "F4", machine_id: "m-1", position_id: "POS-A1", component_id: "C-A1-2", failed_at: "2026-11-12T17:00:00Z",
    restored_at: null, severity: "critical", failure_mode: null, failure_mechanism: "6.4", failure_cause: "5.5",
    detection_method: "production_interference", downtime_event_id: "d-1", work_order_id: "wo-103",
    machine_name: "Estação de Fixação", position_name: "Cilindro A1",
    ...over,
  };
}
