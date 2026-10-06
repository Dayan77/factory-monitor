/**
 * Field gateways (Sprint 5 — docs/gateways.md). Mirrors server/src/api/routes/gateways.py (GatewayOut, …).
 * Token, enrollment code and MQTT password exist in clear only in the response that hands them out (once).
 */
import { api } from "./api";

export type GatewayStatus = "pending" | "online" | "degraded" | "offline";

export interface GatewayDevice {
  id: string;
  protocol: "opcua" | "modbus";
  endpoint?: string;
  host?: string;
  port?: number;
  timestamps?: "gateway" | "source";
  publishing_interval_ms?: number;
  [k: string]: unknown;
}

export interface GatewayHeartbeat {
  at?: string | null;
  uptime_s?: number | null;
  buffer_depth?: number | null;
  errors?: string[] | null;
  config_version?: number | null;
}

export interface Gateway {
  id: string;
  tenant_id: string;
  plant_id: string;
  name: string;
  description: string | null;
  status: GatewayStatus;
  last_seen_at: string | null;
  version: string | null;
  config_version: number;
  config_fetched_at: string | null;
  enrolled: boolean;
  token_prefix: string | null;
  enroll_pending: boolean;
  enroll_expires_at: string | null;
  heartbeat_s: number;
  pulse_poll_ms: number;
  devices: GatewayDevice[];
  tag_count: number;
  last_heartbeat: GatewayHeartbeat | null;
  mqtt_username: string | null;
  created_at: string;
}

export interface GatewayCreateBody {
  name: string;
  plant_id: string;
  description?: string | null;
  devices?: GatewayDevice[];
  heartbeat_s?: number;
  pulse_poll_ms?: number;
}

export type GatewayPatchBody = Partial<Pick<GatewayCreateBody, "name" | "description" | "devices" | "heartbeat_s" | "pulse_poll_ms">>;

export interface EnrollToken {
  gateway_id: string;
  code: string;
  expires_at: string;
  enroll_url?: string;
}

export interface TagAssignment {
  tag_id: string;
  source?: string | null;
  key?: string | null;
  unit_id?: number | null;
  register_type?: "holding" | "input" | "coil" | "discrete" | null;
  data_type_plc?: string | null;
  word_order?: "big" | "little" | null;
  signal_role?: string | null;
}

export interface AssignResult {
  assigned: number;
  config_version: number;
  warnings: string[];
}

export interface ConfigPreview {
  gateway_id: string;
  revision: string;
  config_version: number;
  plant_map: { tags?: { tag_id: string; key: string; source: string; name?: string }[]; sources?: GatewayDevice[]; [k: string]: unknown };
  etag: string;
  warnings: string[];
}

export interface MqttInfo {
  username: string;
  topic_prefix: string;
  acl: string;
  gateways_conf_line: string;
}

export const gatewaysApi = {
  list: (plantId?: string) => api.get<Gateway[]>("/api/gateways/", { params: plantId ? { plant_id: plantId } : undefined }),
  get: (id: string) => api.get<Gateway>(`/api/gateways/${id}`),
  create: (body: GatewayCreateBody) => api.post<Gateway>("/api/gateways/", body),
  update: (id: string, body: GatewayPatchBody) => api.patch<Gateway>(`/api/gateways/${id}`, body),
  remove: (id: string) => api.delete(`/api/gateways/${id}`),
  enrollToken: (id: string) => api.post<EnrollToken>(`/api/gateways/${id}/enroll-token`, {}),
  revoke: (id: string) => api.post<Gateway>(`/api/gateways/${id}/revoke`, {}),
  assignTags: (id: string, tags: TagAssignment[]) => api.put<AssignResult>(`/api/gateways/${id}/tags`, { tags }),
  configPreview: (id: string) => api.get<ConfigPreview>(`/api/gateways/${id}/config-preview`),
  mqtt: (id: string) => api.get<MqttInfo>(`/api/gateways/${id}/mqtt`),
};

/**
 * Install commands shown with the enrollment code. Two steps because the code is exchanged for the device token
 * on the gateway itself (the token never passes through the browser): 1) enroll with curl, 2) run the edge image
 * with the returned gateway_id/token/MQTT credentials. Placeholders in <…> are filled from the step-1 response.
 */
export function installCommands(o: { origin: string; gatewayId: string; tenantId: string; code: string; mqttHost: string }): { enroll: string; run: string } {
  const enroll = `curl -fsS -X POST ${o.origin}/api/gateways/enroll \\\n  -H 'Content-Type: application/json' \\\n  -d '{"code": "${o.code}"}'`;
  const run = [
    "docker run -d --name fm-gateway --restart unless-stopped \\",
    "  -v fm-gateway-data:/data \\",
    `  -e GATEWAY_CONFIG_URL=${o.origin} \\`,
    `  -e GATEWAY_ID=${o.gatewayId} \\`,
    "  -e GATEWAY_TOKEN=<token do passo 1> \\",
    `  -e TENANT_ID=${o.tenantId} \\`,
    `  -e MQTT_BROKER_HOST=${o.mqttHost} \\`,
    "  -e MQTT_USERNAME=<mqtt.username do passo 1> \\",
    "  -e MQTT_PASSWORD=<mqtt.password do passo 1> \\",
    "  -e GATEWAY_CONFIG_CACHE=/data/gateway_config.json \\",
    "  -e BUFFER_DB_PATH=/data/edge_buffer.db -e COUNTER_DB_PATH=/data/edge_counters.db \\",
    "  factory-monitor-edge:latest",
  ].join("\n");
  return { enroll, run };
}

export const GATEWAY_STATUS_LABEL: Record<GatewayStatus, string> = {
  online: "Online",
  degraded: "Degradado",
  offline: "Offline",
  pending: "Aguardando cadastro",
};
