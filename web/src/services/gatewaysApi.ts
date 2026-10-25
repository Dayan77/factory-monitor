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
  /** revoked: the MQTT credential is gone on the server and its readings are discarded; the broker line must go */
  mqtt_revoked?: boolean;
  mqtt_action?: string | null;
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
  acl: string | null;
  gateways_conf_line: string | null;
  revoked?: boolean;
  action?: string;
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
 * Edge image of this release — the same name as infra/gateway/docker-compose.yml (`image:`) and edge/README.md.
 * Change the three together when the edge version changes.
 */
export const EDGE_IMAGE = "factoryconnect-edge:0.5.0";

/**
 * Install commands shown with the enrollment code. One step: the edge enrolls by itself on the first start
 * (ENROLL_CODE + SERVER_URL → POST /api/gateways/enroll) and keeps the device token and the MQTT password in
 * /data/gateway_credentials.json (0600) on its volume — they never go through the browser, the shell history or
 * `docker inspect`. The code is single use (24 h); afterwards it may be removed from the environment.
 */
export function installCommands(o: { origin: string; code: string; mqttHost: string }): { run: string; compose: string } {
  const tls = o.origin.startsWith("https://");
  const run = [
    "docker run -d --name fc-gateway --restart unless-stopped \\",
    "  -v fm-gateway-data:/data \\",
    `  -e SERVER_URL=${o.origin} \\`,
    `  -e ENROLL_CODE=${o.code} \\`,
    `  -e MQTT_BROKER_HOST=${o.mqttHost} -e MQTT_BROKER_PORT=${tls ? 8883 : 1883} -e MQTT_TLS=${tls ? "true" : "false"} \\`,
    `  ${EDGE_IMAGE}`,
  ].join("\n");
  const compose = [
    "# no host do gateway, com o repositório (infra/gateway):",
    "cp gateway.env.example gateway.env",
    "# edite gateway.env:",
    `#   SERVER_URL=${o.origin}`,
    `#   ENROLL_CODE=${o.code}`,
    `#   MQTT_BROKER_HOST=${o.mqttHost}`,
    "docker compose up -d",
    "docker compose logs -f gateway   # procure \"enrollment.done\"",
  ].join("\n");
  return { run, compose };
}

export const GATEWAY_STATUS_LABEL: Record<GatewayStatus, string> = {
  online: "Online",
  degraded: "Degradado",
  offline: "Offline",
  pending: "Aguardando cadastro",
};
