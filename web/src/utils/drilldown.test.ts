import { describe, expect, it } from "vitest";
import { drillApiParams, drillLink, drillPaths, kpiReason, parseDrill } from "./drilldown";

describe("drill-down period context (sprint6 §2.2)", () => {
  it("reads the spec names", () => {
    expect(parseDrill(new URLSearchParams("period=yesterday"))).toEqual({ period: "yesterday" });
    expect(parseDrill(new URLSearchParams("period=current_shift")).period).toBe("current_shift");
  });
  it("accepts the older names of the OEE screen", () => {
    expect(parseDrill(new URLSearchParams("period=7d")).period).toBe("last_7d");
    expect(parseDrill(new URLSearchParams("period=shift")).period).toBe("current_shift");
  });
  it("custom needs start and stop, else falls back", () => {
    expect(parseDrill(new URLSearchParams("period=custom&start=2026-10-01T09:00:00Z&stop=2026-10-02T09:00:00Z")))
      .toEqual({ period: "custom", start: "2026-10-01T09:00:00Z", stop: "2026-10-02T09:00:00Z" });
    expect(parseDrill(new URLSearchParams("period=custom&start=abc")).period).toBe("today");
    expect(parseDrill(new URLSearchParams("period=bogus")).period).toBe("today");
  });
  it("keeps the period on every level link", () => {
    const ctx = parseDrill(new URLSearchParams("period=yesterday"));
    expect(drillLink(drillPaths.line("L1"), ctx)).toBe("/factory/lines/L1?period=yesterday");
    expect(drillLink(drillPaths.machine("M1"), ctx, { pos: "P1" })).toBe("/factory/machines/M1?period=yesterday&pos=P1");
    const custom = { period: "custom" as const, start: "2026-10-01T09:00:00Z", stop: "2026-10-02T09:00:00Z" };
    expect(drillLink("/factory", custom)).toBe("/factory?period=custom&start=2026-10-01T09%3A00%3A00Z&stop=2026-10-02T09%3A00%3A00Z");
  });
  it("lets the server resolve productive days", () => {
    expect(drillApiParams({ period: "today" })).toEqual({ period: "today" });
  });
  it("explains undefined KPIs in Portuguese with a fix shortcut", () => {
    expect(kpiReason("no_planned_time")?.text).toBe("sem tempo planejado neste período");
    expect(kpiReason("no_ideal_cycle_time")?.fix?.label).toBe("cadastrar PRI");
    expect(kpiReason(null)).toBeNull();
  });
});
