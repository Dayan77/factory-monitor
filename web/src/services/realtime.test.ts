import { describe, expect, it } from "vitest";
import {
  applyMachineStatus, parseRealtimeMessage, realtimeSocketParams, reconnectActionFor,
  WS_CLOSE_FORBIDDEN, WS_CLOSE_UNAUTHORIZED,
} from "./realtime";
import { machine } from "@/test/fixtures";

describe("parseRealtimeMessage", () => {
  it("parses a sensor_reading", () => {
    const msg = parseRealtimeMessage(JSON.stringify({
      type: "sensor_reading", tag_id: "tag-1", value: 42.5, unit: "°C", timestamp: "2026-10-05T12:00:00Z",
    }));
    expect(msg).toEqual({ type: "sensor_reading", tag_id: "tag-1", value: 42.5, unit: "°C", timestamp: "2026-10-05T12:00:00Z" });
  });

  it("accepts legacy readings without a type field", () => {
    const msg = parseRealtimeMessage(JSON.stringify({ tag_id: "tag-1", value: 1, timestamp: "x" }));
    expect(msg?.type).toBe("sensor_reading");
  });

  it("parses a machine_status message", () => {
    const msg = parseRealtimeMessage(JSON.stringify({
      type: "machine_status", machine_id: "m-1", machine_name: "Torno", status: "unknown",
      previous_status: "running", last_seen_at: null, timestamp: "2026-10-05T12:00:00Z",
    }));
    expect(msg).toMatchObject({ type: "machine_status", machine_id: "m-1", status: "unknown", last_seen_at: null });
  });

  it.each([
    ["not json", "{"],
    ["a primitive", "3"],
    ["an unknown type", JSON.stringify({ type: "hello" })],
    ["a reading without value", JSON.stringify({ type: "sensor_reading", tag_id: "t" })],
    ["a status with an invalid status", JSON.stringify({ type: "machine_status", machine_id: "m", status: "on fire" })],
  ])("rejects %s", (_name, raw) => {
    expect(parseRealtimeMessage(raw)).toBeNull();
  });
});

describe("applyMachineStatus", () => {
  const list = [machine({ id: "m-1", status: "running", last_seen_at: "2026-10-05T11:00:00Z" }), machine({ id: "m-2" })];

  it("updates status and last_seen_at of the matching machine only", () => {
    const next = applyMachineStatus(list, { machine_id: "m-1", status: "unknown", last_seen_at: "2026-10-05T11:30:00Z" });
    expect(next?.[0]).toMatchObject({ status: "unknown", last_seen_at: "2026-10-05T11:30:00Z" });
    expect(next?.[1]).toBe(list[1]);
  });

  it("keeps the previous last_seen_at when the message has none", () => {
    const next = applyMachineStatus(list, { machine_id: "m-1", status: "unknown", last_seen_at: null });
    expect(next?.[0].last_seen_at).toBe("2026-10-05T11:00:00Z");
  });

  it("returns the same reference when nothing matches (no re-render)", () => {
    expect(applyMachineStatus(list, { machine_id: "zzz", status: "error" })).toBe(list);
    expect(applyMachineStatus(undefined, { machine_id: "m-1", status: "error" })).toBeUndefined();
  });
});

describe("realtime socket auth", () => {
  it("sends the token as a subprotocol, never in the URL", () => {
    const p = realtimeSocketParams("t-1", "jwt.abc.def", { protocol: "https:", host: "app.example" });
    expect(p.url).toBe("wss://app.example/ws/sensors/t-1");
    expect(p.url).not.toContain("jwt");
    expect(p.protocols).toEqual(["bearer", "jwt.abc.def"]);
    expect(realtimeSocketParams("t-1", "x", { protocol: "http:", host: "localhost:5173" }).url)
      .toBe("ws://localhost:5173/ws/sensors/t-1");
  });

  it("maps close codes to reconnect actions", () => {
    expect(reconnectActionFor(WS_CLOSE_UNAUTHORIZED)).toBe("refresh-then-retry");
    expect(reconnectActionFor(WS_CLOSE_FORBIDDEN)).toBe("stop");
    expect(reconnectActionFor(1006)).toBe("retry");
    expect(reconnectActionFor(1000)).toBe("retry");
  });
});
