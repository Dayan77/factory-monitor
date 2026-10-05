import { describe, expect, it } from "vitest";
import { notificationTarget, parsePreferences, serializePreferences } from "./notifications";
import { normalizeFailureCodes, rowsOf } from "@/services/maintenanceApi";
import { parseNotification, parseRealtimeMessage } from "@/services/realtime";
import type { AppNotification } from "@/services/maintenanceTypes";

const n = (over: Partial<AppNotification>): AppNotification => ({ id: "n", type: "x", title: "t", body: null, created_at: "2026-12-01T10:00:00Z", read_at: null, ...over });

describe("preferences (GET {kinds, digest_enabled} → PUT {channels, digest_enabled})", () => {
  const server = {
    kinds: [
      { kind: "wo_assigned", label: "OS atribuída", channels: { in_app: true, email: false, web_push: true }, mutable: true, digest: false },
      { kind: "emergency_call", label: "Chamado emergencial", channels: { in_app: true, email: false, web_push: true }, mutable: false },
    ],
    digest_enabled: true,
  };

  it("parses rows; emergency is locked", () => {
    const m = parsePreferences(server);
    expect(m.rows).toHaveLength(2);
    expect(m.rows[0]).toMatchObject({ event: "wo_assigned", label: "OS atribuída", channels: { in_app: true, email: false, web_push: true }, locked: false });
    expect(m.rows[1].locked).toBe(true);
    expect(m.digest_enabled).toBe(true);
  });

  it("serializes only mutable kinds", () => {
    const m = parsePreferences(server);
    m.rows[0].channels.email = true;
    expect(serializePreferences(m)).toEqual({ channels: { wo_assigned: { in_app: true, email: true, web_push: true } }, digest_enabled: true });
  });

  it("accepts a bare event → channels map with alias names", () => {
    const m = parsePreferences({ wo_assigned: { bell: true, push: false } });
    expect(m.rows[0].channels).toEqual({ in_app: true, web_push: false });
  });
});

describe("notification target (each notification opens the screen that answers it)", () => {
  it("maps server links for desktop and mobile", () => {
    expect(notificationTarget(n({ url: "/maintenance/work-orders/wo-9" }), false)).toBe("/maintenance/work-orders/wo-9");
    expect(notificationTarget(n({ url: "/maintenance/work-orders/wo-9" }), true)).toBe("/m/work-orders/wo-9");
    expect(notificationTarget(n({ url: "/m/work-orders/wo-9" }), false)).toBe("/maintenance/work-orders/wo-9");
    expect(notificationTarget(n({ work_order_id: "wo-2" }), false)).toBe("/maintenance/work-orders?wo=wo-2");
    expect(notificationTarget(n({ type: "plan_due" }), false)).toBe("/maintenance/upcoming");
  });
});

describe("normalization of server payloads", () => {
  it("notification: kind/link/priority/data/read → UI shape", () => {
    const out = parseNotification({ id: "1", kind: "emergency_call", title: "Robô", body: "falha", link: "/m/work-orders/w", priority: "emergency", data: { work_order_id: "w" }, read: true, created_at: "2026-12-01T10:00:00Z" });
    expect(out).toMatchObject({ type: "emergency_call", url: "/m/work-orders/w", severity: "emergency", work_order_id: "w" });
    expect(out?.read_at).not.toBeNull();
    expect(parseNotification({ id: 1 })).toBeNull();
  });

  it("WebSocket {type: 'notification'} is parsed", () => {
    const msg = parseRealtimeMessage(JSON.stringify({ type: "notification", notification: { id: "a", kind: "wo_assigned", title: "OS" } }));
    expect(msg).toMatchObject({ type: "notification", notification: { id: "a", type: "wo_assigned" } });
    expect(parseRealtimeMessage(JSON.stringify({ type: "notification", notification: {} }))).toBeNull();
  });

  it("failure codes: modes_by_component_type tags the modes", () => {
    const c = normalizeFailureCodes({
      failure_modes: [{ code: "ELP", label: "Vazamento externo" }, { code: "VIB", label: "Vibração" }],
      mechanisms: [{ code: "2.4", label: "Desgaste" }], causes: { "5.5": "Desconhecida" }, detection_methods: [],
      modes_by_component_type: { pneumatic_cylinder: ["ELP"] },
    });
    expect(c.modes[0].component_types).toEqual(["pneumatic_cylinder"]);
    expect(c.modes[1].component_types).toBeNull();
    expect(c.causes).toEqual([{ code: "5.5", label: "Desconhecida" }]);
  });

  it("list wrappers: bare list, {rows}, {items}", () => {
    expect(rowsOf([1])).toEqual([1]);
    expect(rowsOf({ rows: [2] })).toEqual([2]);
    expect(rowsOf({ unread_count: 1, items: [3] })).toEqual([3]);
    expect(rowsOf(null)).toEqual([]);
  });
});
