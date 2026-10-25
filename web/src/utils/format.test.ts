import { describe, expect, it } from "vitest";
import { formatDate, formatDec1, formatInt, formatMeasure, formatPct } from "./format";
import { fillHourly } from "./series";

describe("format", () => {
  it("formats pt-BR numbers and fractions as percent (no truncation)", () => {
    expect(formatInt(3600000)).toBe("3.600.000");
    expect(formatDec1(12.345)).toBe("12,3");
    // kpis.md S3-5: 1 decimal, pt-BR, half-up from the raw value
    expect(formatPct(0.724)).toBe("72,4\u00a0%");
    expect(formatPct(1.05)).toBe("105,0\u00a0%");
    expect(formatPct(0.769186)).toBe("76,9\u00a0%");
    expect(formatPct(0.74449)).toBe("74,4\u00a0%");
    expect(formatPct(0.7365)).toBe("73,7\u00a0%");
    expect(formatPct(null)).toBe("—");
    expect(formatMeasure("hours", 2.5)).toBe("2,5 h");
  });

  it("keeps date-only strings on the same calendar day", () => {
    expect(formatDate("2026-12-14")).toBe("14/12/2026");
    expect(formatDate(null)).toBe("—");
  });
});

describe("fillHourly", () => {
  it("fills missing hours with zero buckets", () => {
    const out = fillHourly(
      [{ t: "2026-10-05T01:00:00.000Z", value: 5 }],
      "2026-10-05T00:00:00Z",
      "2026-10-05T03:00:00Z",
      (t) => ({ t, value: 0 }),
    );
    expect(out.map((p) => p.value)).toEqual([0, 5, 0]);
    expect(out[0].t).toBe("2026-10-05T00:00:00.000Z");
  });

  it("returns points untouched without a window", () => {
    const pts = [{ t: "x", value: 1 }];
    expect(fillHourly(pts, undefined, undefined, (t) => ({ t, value: 0 }))).toBe(pts);
  });
});
