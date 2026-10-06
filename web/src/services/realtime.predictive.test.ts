import { describe, expect, it } from "vitest";
import { parseRealtimeMessage } from "./realtime";
import { normalizeOverview } from "./efficiency";

describe("predictive_alert socket message (Sprint 5)", () => {
  it("parses the alert summary", () => {
    const m = parseRealtimeMessage(JSON.stringify({ type: "predictive_alert", event: "escalated", alert: { id: "a1", severity: "critical", state: "open", position_id: "p", machine_id: "m", explanation: "x" } }));
    expect(m).toEqual({ type: "predictive_alert", event: "escalated", alert: { id: "a1", severity: "critical", state: "open", position_id: "p", machine_id: "m" } });
  });
  it("rejects a message without alert id", () => {
    expect(parseRealtimeMessage(JSON.stringify({ type: "predictive_alert", alert: {} }))).toBeNull();
  });
});

describe("overview gateways", () => {
  it("normalizes the gateways of the plant; absent on older servers", () => {
    expect(normalizeOverview({ gateways: [{ id: "g", name: "GW", status: "offline", version: null, last_seen_at: null, config_version: 3 }] }).gateways)
      .toEqual([{ id: "g", name: "GW", status: "offline", version: null, last_seen_at: null, config_version: 3 }]);
    expect(normalizeOverview({}).gateways).toBeUndefined();
  });
});
