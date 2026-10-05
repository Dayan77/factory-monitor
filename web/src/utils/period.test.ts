import { describe, expect, it } from "vitest";
import { currentShiftInstance, localDate, resolvePeriod, zonedToUtc } from "./period";
import { shift } from "@/test/fixtures";

const TZ = "America/Sao_Paulo"; // UTC−3, no DST since 2019
// Monday 2026-10-05 15:20 local = 18:20Z
const NOW = Date.UTC(2026, 9, 5, 18, 20);

describe("zonedToUtc / localDate", () => {
  it("converts plant wall time to UTC", () => {
    expect(new Date(zonedToUtc(2026, 10, 5, 0, 0, TZ)).toISOString()).toBe("2026-10-05T03:00:00.000Z");
    expect(new Date(zonedToUtc(2026, 10, 5, 6, 0, "America/Manaus")).toISOString()).toBe("2026-10-05T10:00:00.000Z");
    expect(new Date(zonedToUtc(2026, 7, 1, 0, 0, "Europe/Berlin")).toISOString()).toBe("2026-06-30T22:00:00.000Z");
  });
  it("gives the plant-local date", () => {
    expect(localDate(Date.UTC(2026, 9, 5, 2, 0), TZ)).toBe("2026-10-04");
    expect(localDate(NOW, TZ)).toBe("2026-10-05");
  });
});

describe("resolvePeriod", () => {
  it("today / yesterday / 7 and 30 days start at the plant's local midnight", () => {
    expect(resolvePeriod("today", { now: NOW, tz: TZ })).toMatchObject({ start: "2026-10-05T03:00:00.000Z", stop: new Date(NOW).toISOString(), groupBy: "hour" });
    expect(resolvePeriod("yesterday", { now: NOW, tz: TZ })).toMatchObject({ start: "2026-10-04T03:00:00.000Z", stop: "2026-10-05T03:00:00.000Z" });
    expect(resolvePeriod("7d", { now: NOW, tz: TZ })).toMatchObject({ start: "2026-09-29T03:00:00.000Z", groupBy: "day" });
    expect(resolvePeriod("30d", { now: NOW, tz: TZ }).start).toBe("2026-09-06T03:00:00.000Z");
  });
  it("custom covers whole local days, capped at now", () => {
    const p = resolvePeriod("custom", { now: NOW, tz: TZ, customFrom: "2026-10-01", customTo: "2026-10-02" });
    expect(p).toMatchObject({ start: "2026-10-01T03:00:00.000Z", stop: "2026-10-03T03:00:00.000Z", groupBy: "hour" });
    expect(resolvePeriod("custom", { now: NOW, tz: TZ, customFrom: "2026-10-05", customTo: "2026-10-09" }).stop).toBe(new Date(NOW).toISOString());
    expect(resolvePeriod("custom", { now: NOW, tz: TZ }).unavailable).toBeTruthy();
    expect(resolvePeriod("custom", { now: NOW, tz: TZ, customFrom: "2026-10-09", customTo: "2026-10-09" }).unavailable).toBeTruthy();
  });
  it("current shift: the running shift instance, or an explicit unavailable state", () => {
    const shifts = [shift(), shift({ id: "s-2", name: "Turno B", start_local: "14:00", end_local: "22:00" })];
    expect(resolvePeriod("shift", { now: NOW, tz: TZ, shifts })).toMatchObject({
      start: "2026-10-05T17:00:00.000Z", stop: new Date(NOW).toISOString(), label: "Turno B · 14:00–22:00",
    });
    expect(resolvePeriod("shift", { now: NOW, tz: TZ, shifts: [] }).unavailable).toBe("Nenhum turno cadastrado.");
    expect(resolvePeriod("shift", { now: NOW, tz: TZ, shifts: [shift()] }).unavailable).toBe("Nenhum turno em andamento agora.");
  });
});

describe("currentShiftInstance", () => {
  const night = shift({ id: "n", name: "Noite", start_local: "22:00", end_local: "06:00", weekdays: [0, 1, 2, 3, 4] });
  it("a shift crossing midnight belongs to the productive day it started", () => {
    // Tuesday 2026-10-06 02:00 local → started Monday 22:00
    const inst = currentShiftInstance([night], Date.UTC(2026, 9, 6, 5, 0), TZ);
    expect(inst?.productiveDay).toBe("2026-10-05");
    expect(new Date(inst!.start).toISOString()).toBe("2026-10-06T01:00:00.000Z");
    expect(new Date(inst!.end).toISOString()).toBe("2026-10-06T09:00:00.000Z");
  });
  it("respects weekdays and inactive shifts", () => {
    // Saturday 2026-10-10 02:00 local → started Friday (weekday 4) 22:00: valid
    expect(currentShiftInstance([night], Date.UTC(2026, 9, 10, 5, 0), TZ)?.productiveDay).toBe("2026-10-09");
    // Sunday 2026-10-11 02:00 → started Saturday (5): not scheduled
    expect(currentShiftInstance([night], Date.UTC(2026, 9, 11, 5, 0), TZ)).toBeNull();
    expect(currentShiftInstance([{ ...night, is_active: false }], Date.UTC(2026, 9, 6, 5, 0), TZ)).toBeNull();
  });
});
