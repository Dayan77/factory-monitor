import { describe, expect, it } from "vitest";
import type { CatalogEntry } from "@/services/sprint6Types";
import { cadenceLabel, emptyScheduleForm, filenameFrom, scheduleBody, validateSchedule, weekStart } from "./reports";

const weekly: CatalogEntry = {
  code: "weekly_oee", name: "Revisão semanal de OEE", title: "", question: "", scope: ["plant", "line"], period: "week",
  formats: ["pdf", "xlsx"], settle_min: 30, deliver_at_local: "07:00", weekday: 0, month_day: null,
  recipients: ["manager", "production_supervisor"], landscape: true, screen: "/oee", allowed: true,
};
const shift: CatalogEntry = { ...weekly, code: "shift", scope: ["line"], period: "shift_instance", formats: ["pdf"], deliver_at_local: null, weekday: null };
const monthly: CatalogEntry = { ...weekly, code: "monthly_maintenance", scope: ["plant"], period: "month", weekday: null, month_day: 1 };

describe("reports helpers", () => {
  it("reads the filename of Content-Disposition", () => {
    expect(filenameFrom('attachment; filename="oee_2026-W40.pdf"', "x")).toBe("oee_2026-W40.pdf");
    expect(filenameFrom("attachment; filename*=UTF-8''produ%C3%A7%C3%A3o.xlsx", "x")).toBe("produção.xlsx");
    expect(filenameFrom(null, "fallback.pdf")).toBe("fallback.pdf");
  });
  it("computes the Monday of a week", () => {
    expect(weekStart("2026-10-07")).toBe("2026-10-05");
    expect(weekStart("2026-10-05")).toBe("2026-10-05");
    expect(weekStart("2026-10-04")).toBe("2026-09-28");
  });
  it("starts the form with the catalog defaults", () => {
    const f = emptyScheduleForm(weekly);
    expect(f.formats).toEqual(["pdf", "xlsx"]);
    expect(f.roles).toEqual(["manager", "production_supervisor"]);
    expect(f.deliver_at_local).toBe("07:00");
  });
  it("validates like the server", () => {
    const f = { ...emptyScheduleForm(weekly), scope_type: "line" as const, scope_id: "" };
    expect(validateSchedule(f, weekly).scope_id).toBe("Escolha a linha.");
    expect(validateSchedule({ ...f, scope_id: "L1", deliver_at_local: "7h" }, weekly).deliver_at_local).toBeDefined();
    expect(validateSchedule({ ...f, scope_id: "L1", formats: [] }, weekly).formats).toBeDefined();
    expect(validateSchedule({ ...emptyScheduleForm(shift), scope_id: "L1", formats: ["xlsx"] }, shift).formats).toMatch(/PDF/);
    expect(validateSchedule({ ...emptyScheduleForm(monthly), scope_id: "P", month_day: "31" }, monthly).month_day).toBeDefined();
    expect(validateSchedule({ ...f, scope_id: "L1", roles: [] }, weekly).roles).toBeDefined();
    expect(validateSchedule({ ...f, scope_id: "L1" }, weekly)).toEqual({});
  });
  it("builds the API body", () => {
    const b = scheduleBody({ ...emptyScheduleForm(weekly), scope_type: "plant", scope_id: "P1", weekday: "2" }, weekly);
    expect(b).toMatchObject({ report_type: "weekly_oee", scope_type: "plant", scope_id: "P1", weekday: 2, deliver_at_local: "07:00" });
    expect(b.recipients?.roles).toEqual(["manager", "production_supervisor"]);
    const s = scheduleBody({ ...emptyScheduleForm(shift), scope_id: "L1" }, shift);
    expect(s.deliver_at_local).toBeUndefined();
  });
  it("describes the cadence", () => {
    expect(cadenceLabel({ report_type: "weekly_oee", deliver_at_local: "07:00", weekday: 0, month_day: null })).toBe("toda segunda às 07:00");
    expect(cadenceLabel({ report_type: "shift", deliver_at_local: null, weekday: null, month_day: null })).toBe("ao fim de cada turno + 15 min");
  });
});
