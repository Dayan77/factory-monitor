import { describe, expect, it } from "vitest";
import { formatAgo, formatSeenAgo, secondsSince } from "./time";

const NOW = Date.parse("2026-10-05T12:00:00Z");

describe("time helpers", () => {
  it("computes seconds since an ISO timestamp", () => {
    expect(secondsSince("2026-10-05T11:59:48Z", NOW)).toBe(12);
    expect(secondsSince(null, NOW)).toBeNull();
    expect(secondsSince(undefined, NOW)).toBeNull();
    expect(secondsSince("not a date", NOW)).toBeNull();
  });

  it("clamps future timestamps (clock skew) to 0", () => {
    expect(secondsSince("2026-10-05T12:00:05Z", NOW)).toBe(0);
  });

  it("formats durations", () => {
    expect(formatAgo(0)).toBe("há 0 s");
    expect(formatAgo(59)).toBe("há 59 s");
    expect(formatAgo(60)).toBe("há 1 min");
    expect(formatAgo(3599)).toBe("há 59 min");
    expect(formatAgo(7200)).toBe("há 2 h");
    expect(formatAgo(3 * 86400)).toBe("há 3 d");
  });

  it("formats the machine freshness label", () => {
    expect(formatSeenAgo("2026-10-05T11:59:48Z", NOW)).toBe("visto há 12 s");
    expect(formatSeenAgo(null, NOW)).toBe("nunca visto");
  });
});
