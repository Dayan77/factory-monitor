import { describe, expect, it } from "vitest";
import {
  analysisPending, can, daysToDue, dueTrigger, isBreakIn, isPastDue, isWoOverdue, localAllowedTransitions, mondayOf,
  nextDueUsageLabel, offeredTransitions, planStateFor, remainingHours, shortModes, sortForTechnician, sortPlans,
  transitionLabel, ISO_MODES, hoursLabel,
} from "./maintenance";
import { OPERATOR, SUP, TECH, planStatus, user, workOrder } from "@/test/maintenanceFixtures";

describe("state machine (S4-5)", () => {
  it("follows the §5.3 diagram, cancel only before completed", () => {
    expect(localAllowedTransitions("draft", "preventive", null)).toEqual(["open", "cancelled"]);
    expect(localAllowedTransitions("on_hold", "preventive", null)).toEqual(["in_progress", "scheduled", "cancelled"]);
    expect(localAllowedTransitions("completed", "preventive", null)).toEqual(["closed"]);
    expect(localAllowedTransitions("closed", "preventive", null)).toEqual([]);
  });

  it("open → in_progress only for an emergency corrective", () => {
    expect(localAllowedTransitions("open", "corrective", "emergency")).toContain("in_progress");
    expect(localAllowedTransitions("open", "corrective", "deferred")).not.toContain("in_progress");
    expect(localAllowedTransitions("open", "preventive", null)).not.toContain("in_progress");
  });
});

describe("offeredTransitions — the UI only offers allowed transitions", () => {
  it("trusts the server's user_allowed_transitions (subset of allowed)", () => {
    const wo = workOrder({ state: "scheduled", allowed_transitions: ["in_progress", "cancelled"], user_allowed_transitions: ["cancelled"] });
    expect(offeredTransitions(wo, TECH)).toEqual(["cancelled"]);
  });

  it("without the server list, filters allowed transitions by role", () => {
    const wo = workOrder({ state: "scheduled", allowed_transitions: ["in_progress", "cancelled"] });
    expect(offeredTransitions(wo, TECH)).toEqual(["in_progress"]);
    expect(offeredTransitions(wo, user())).toEqual(["cancelled"]);
    expect(offeredTransitions(workOrder({ state: "completed", allowed_transitions: ["closed"] }), SUP)).toEqual(["closed"]);
    expect(offeredTransitions(workOrder({ state: "completed", allowed_transitions: ["closed"] }), TECH)).toEqual([]);
  });

  it("labels depend on the origin state", () => {
    expect(transitionLabel("open", "in_progress")).toBe("Assumir");
    expect(transitionLabel("on_hold", "in_progress")).toBe("Retomar");
    expect(transitionLabel("on_hold", "scheduled")).toBe("Reprogramar");
    expect(transitionLabel("completed", "closed")).toBe("Fechar OS");
  });
});

describe("roles", () => {
  it("roles[] and the legacy role both count", () => {
    expect(can({ role: "operator", roles: ["operator", "technician"] }, "maintenance.execute")).toBe(true);
    expect(can({ role: "manager" }, "maintenance.plan")).toBe(true);
    expect(can(OPERATOR, "maintenance.view")).toBe(false);
    expect(can(OPERATOR, "maintenance.request")).toBe(true);
    expect(can(SUP, "maintenance.close")).toBe(true);
    expect(can(TECH, "maintenance.plan")).toBe(false);
    expect(can(null, "maintenance.view")).toBe(false);
  });
});

describe("work orders", () => {
  it("overdue = scheduled_for < today and before completed", () => {
    expect(isWoOverdue(workOrder({ scheduled_for: "2026-11-28", state: "scheduled" }), "2026-12-01")).toBe(true);
    expect(isWoOverdue(workOrder({ scheduled_for: "2026-12-01", state: "scheduled" }), "2026-12-01")).toBe(false);
    expect(isWoOverdue(workOrder({ scheduled_for: "2026-11-28", state: "completed" }), "2026-12-01")).toBe(false);
  });

  it("break-in (encaixe): scheduled on/after Monday 00:00 of the week of scheduled_for (S4-6)", () => {
    expect(mondayOf("2026-12-03")).toBe("2026-11-30");
    const at = (day: string) => [{ at: `${day}T15:00:00Z`, by: null, from_state: "approved" as const, to_state: "scheduled" as const, note: null }];
    expect(isBreakIn({ scheduled_for: "2026-12-03", history: at("2026-11-27") }, "UTC")).toBe(false);
    expect(isBreakIn({ scheduled_for: "2026-12-03", history: at("2026-11-30") }, "UTC")).toBe(true);
  });

  it("remaining hours: technician's estimate, else max(estimated − labor, 0)", () => {
    expect(remainingHours({ remaining_hours: 6, estimated_hours: 8, labor_hours: 2 })).toBe(6);
    expect(remainingHours({ remaining_hours: null, estimated_hours: 1.5, labor_hours: 2 })).toBe(0);
    expect(remainingHours({ remaining_hours: null, estimated_hours: 6, labor_hours: 1.5 })).toBe(4.5);
  });

  it("technician order: emergency > overdue > in progress > today > next", () => {
    const today = "2026-12-01";
    const list = sortForTechnician([
      workOrder({ id: "a", number: "OS-5", scheduled_for: "2026-12-04" }),
      workOrder({ id: "b", number: "OS-4", scheduled_for: today }),
      workOrder({ id: "c", number: "OS-3", scheduled_for: "2026-11-28" }),
      workOrder({ id: "d", number: "OS-2", type: "corrective", urgency: "emergency", state: "open", scheduled_for: null }),
      workOrder({ id: "e", number: "OS-1", state: "in_progress", scheduled_for: today }),
    ], today);
    expect(list.map((w) => w.id)).toEqual(["d", "c", "e", "b", "a"]);
  });

  it("hours keep quarter hours", () => {
    expect(hoursLabel(0.75)).toBe("0,75 h");
    expect(hoursLabel(18)).toBe("18,0 h");
    expect(hoursLabel(null)).toBe("—");
  });
});

describe("plans (S4-3)", () => {
  it("due trigger = earliest date (PL3: 82 % by starts, due by hours)", () => {
    const p = planStatus();
    expect(p.determining_trigger).toBe("starts");
    expect(dueTrigger(p)?.measure).toBe("hours");
    expect(dueTrigger({ ...p, due_trigger: "days" })?.measure).toBe("days"); // server wins
  });

  it("ties on date → larger consumed; no dates → null", () => {
    const p = planStatus({ triggers: [
      { measure: "hours", interval: 1, usage: 0.5, consumed: 0.5, projected_date: "2027-01-10" },
      { measure: "starts", interval: 1, usage: 0.7, consumed: 0.7, projected_date: "2027-01-10" },
    ] });
    expect(dueTrigger(p)?.measure).toBe("starts");
    expect(dueTrigger(planStatus({ triggers: [{ measure: "hours", interval: 1, usage: 0, consumed: 0, projected_date: null }] }))).toBeNull();
  });

  it("days to due: server value, else from the date; past due = 0", () => {
    expect(daysToDue(planStatus(), "2026-12-01")).toBe(40);
    expect(daysToDue(planStatus({ days_to_due: 7 }), "2026-12-01")).toBe(7);
    expect(daysToDue(planStatus({ consumed: 1.03 }), "2026-12-01")).toBe(0);
    expect(isPastDue(planStatus({ consumed: 1.03 }))).toBe(true);
    expect(isPastDue(planStatus({ consumed: null }))).toBe(false);
  });

  it("sorts by days to due, then severity", () => {
    const list = sortPlans([
      planStatus({ plan_id: "ok", state: "ok", consumed: 0.17, projected_date: "2027-03-04" }),
      planStatus({ plan_id: "over", state: "overdue", consumed: 3.7, projected_date: "2026-12-01" }),
      planStatus({ plan_id: "pl3" }),
      planStatus({ plan_id: "due", state: "due", consumed: 1.03, projected_date: "2026-12-01" }),
    ], "2026-12-01");
    expect(list.map((p) => p.plan_id)).toEqual(["over", "due", "pl3", "ok"]);
  });

  it("next due usage is the absolute counter value", () => {
    expect(nextDueUsageLabel({ measure: "hours", value: 45800 })).toBe("45.800 h no horímetro");
    expect(nextDueUsageLabel({ measure: "cycles", value: 9320000 })).toBe("9.320.000 ciclos no contador");
    expect(nextDueUsageLabel(null)).toBeNull();
  });

  it("state from consumed (domain §5.1)", () => {
    expect(planStateFor(0.79)).toBe("ok");
    expect(planStateFor(0.8)).toBe("due_soon");
    expect(planStateFor(1.0)).toBe("due");
    expect(planStateFor(1.1)).toBe("overdue");
  });
});

describe("ISO 14224", () => {
  it("short list of modes per component type; server-tagged modes win", () => {
    expect(shortModes(ISO_MODES, "pneumatic_cylinder").map((m) => m.code).slice(0, 4)).toEqual(["ELP", "INL", "PDE", "BRD"]);
    const tagged = [{ code: "XYZ", label: "x", component_types: ["valve"] }, ...ISO_MODES];
    expect(shortModes(tagged, "valve").map((m) => m.code)).toEqual(["XYZ"]);
    expect(shortModes(ISO_MODES, null)).toEqual([]);
  });

  it("analysis pending while mechanism 6.4 / cause 5.5 / no mode", () => {
    expect(analysisPending({ failure_mode: "ELP", failure_mechanism: "6.4", failure_cause: "3.4" })).toBe(true);
    expect(analysisPending({ failure_mode: "ELP", failure_mechanism: "2.4", failure_cause: "5.5" })).toBe(true);
    expect(analysisPending({ failure_mode: "ELP", failure_mechanism: "2.4", failure_cause: "3.4" })).toBe(false);
  });
});
