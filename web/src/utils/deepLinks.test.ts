import { describe, expect, it } from "vitest";
import { isoWindowToDays, normalizeCatalogParams } from "./deepLinks";

const TZ = "America/Manaus"; // UTC−4
const run = (path: string, q: string) => normalizeCatalogParams(path, new URLSearchParams(q), TZ)?.toString() ?? null;

describe("AI citation deep links → screen URL state", () => {
  it("OEE: yesterday by shift (plant-local day) → custom period + group", () => {
    expect(run("/oee", "scope=line&id=L&start=2026-10-05T04:00:00Z&stop=2026-10-06T04:00:00Z&group_by=shift"))
      .toBe("scope=line&id=L&period=custom&from=2026-10-05&to=2026-10-05&group=shift");
    expect(run("/oee", "scope=line&id=L&start=2026-10-05T04:00:00Z&stop=2026-10-06T04:00:00Z&group_by=none")).toBe("scope=line&id=L&period=custom&from=2026-10-05&to=2026-10-05");
    expect(run("/oee", "scope=line&id=L&period=today")).toBeNull();
  });
  it("stops, shift report, reliability, KPIs, work orders", () => {
    expect(run("/stops", "line_id=L&start=2026-10-05T04:00:00Z&stop=2026-10-05T10:00:00Z&unjustified=true")).toBe("line=L");
    expect(run("/reports/shift", "line_id=L&day=2026-10-05&shift_id=S")).toBe("day=2026-10-05&line=L&shift=S");
    expect(run("/maintenance/reliability", "group_by=manufacturer")).toBe("group=manufacturer");
    expect(run("/maintenance/kpis", "start=2026-09-01T04:00:00Z&stop=2026-10-01T04:00:00Z")).toBe("month=2026-09");
    expect(run("/maintenance/work-orders", "overdue=true&assignee_id=me")).toBe("assignee=me&quick=overdue");
    expect(run("/maintenance/work-orders", "wo=x")).toBeNull();
    expect(run("/maintenance/predictive", "alert=a")).toBeNull();
  });
  it("window to local days (stop exclusive)", () => {
    expect(isoWindowToDays("2026-10-01T04:00:00Z", "2026-10-08T04:00:00Z", TZ)).toEqual({ from: "2026-10-01", to: "2026-10-07" });
  });
});
