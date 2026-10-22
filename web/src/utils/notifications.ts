/**
 * Notification helpers: target route of a notification and the preferences normalizer.
 * GET/PUT /api/notifications/preferences — the UI accepts the common shapes and writes back in the
 * same shape it read (list of rows or event → channels map), so it never invents fields.
 */
import type { AppNotification } from "@/services/maintenanceTypes";

export type Channel = "in_app" | "web_push" | "email";
export const CHANNELS: Channel[] = ["in_app", "web_push", "email"];
export const CHANNEL_LABEL: Record<Channel, string> = { in_app: "Sino", web_push: "Push", email: "E-mail" };
/** Older/alternative channel names accepted on read. */
const ALIASES: Record<string, Channel> = { bell: "in_app", push: "web_push" };

export const EVENT_LABEL: Record<string, string> = {
  plan_due_soon: "Plano ≥ 80 % (rascunho criado)",
  plan_due: "Plano vencido",
  plan_overdue: "Plano além da tolerância",
  component_life: "Componente ≥ 80 % / 100 % da vida",
  wo_assigned: "OS atribuída a mim",
  wo_rescheduled: "OS reprogramada",
  emergency_call: "Chamado emergencial (plantão)",
  failure_escalation: "Falha aberta há mais de 2 × MTTR",
  wo_overdue: "OS vencida",
  wo_on_hold: "OS em espera",
  window_request: "Pedido de janela de parada",
  wo_awaiting_close: "OS concluída aguardando fechamento",
  failure_new: "Falha nova registrada",
};

export function eventLabel(e: string): string {
  return EVENT_LABEL[e] ?? e.replace(/_/g, " ");
}

/** Emergency calls cannot be muted (workflows §5). */
export function isLockedEvent(e: string): boolean {
  return /emergenc/i.test(e);
}

export interface PrefRow {
  event: string;
  label: string;
  channels: Partial<Record<Channel, boolean>>;
  locked: boolean;
  /** E-mail goes in the 07:00 daily summary. */
  digest?: boolean;
}

export interface PrefModel {
  rows: PrefRow[];
  digest_enabled: boolean | null;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function channelsOf(o: unknown): Partial<Record<Channel, boolean>> {
  const out: Partial<Record<Channel, boolean>> = {};
  if (!isRecord(o)) return out;
  for (const [k, v] of Object.entries(o)) {
    const c = (CHANNELS as string[]).includes(k) ? (k as Channel) : ALIASES[k];
    if (c && typeof v === "boolean") out[c] = v;
  }
  return out;
}

/**
 * GET /api/notifications/preferences → `{kinds: [{kind, label, channels: {in_app, email, web_push}, mutable, digest}],
 * digest_enabled}`. Also accepts a bare event → channels map.
 */
export function parsePreferences(data: unknown): PrefModel {
  const d = isRecord(data) ? data : {};
  const digest_enabled = typeof d.digest_enabled === "boolean" ? d.digest_enabled : null;
  const list = Array.isArray(d.kinds) ? d.kinds : Array.isArray(d.preferences) ? d.preferences : null;
  if (list) {
    const rows = list.flatMap((it): PrefRow[] => {
      if (!isRecord(it)) return [];
      const event = typeof it.kind === "string" ? it.kind : typeof it.event_type === "string" ? it.event_type : null;
      if (!event) return [];
      return [{
        event,
        label: typeof it.label === "string" ? it.label : eventLabel(event),
        channels: channelsOf(it.channels ?? it),
        locked: it.mutable === false || isLockedEvent(event),
        digest: it.digest === true,
      }];
    });
    return { rows, digest_enabled };
  }
  const map = isRecord(d.channels) ? d.channels : d;
  const rows = Object.entries(map).filter(([k, v]) => k !== "digest_enabled" && isRecord(v))
    .map(([event, v]) => ({ event, label: eventLabel(event), channels: channelsOf(v), locked: isLockedEvent(event) }));
  return { rows, digest_enabled };
}

/** PUT body: `{channels: {kind: {in_app, email, web_push}}, digest_enabled}` (locked kinds are not sent). */
export function serializePreferences(m: PrefModel): { channels: Record<string, Partial<Record<Channel, boolean>>>; digest_enabled?: boolean } {
  const channels = Object.fromEntries(m.rows.filter((r) => !r.locked).map((r) => [r.event, { ...r.channels }]));
  return m.digest_enabled == null ? { channels } : { channels, digest_enabled: m.digest_enabled };
}

/** Route that answers a notification (workflows §5: "cada notificação abre a tela que responde a ela"). */
export function notificationTarget(n: AppNotification, mobile: boolean): string {
  if (n.url) {
    const wo = /^\/maintenance\/work-orders\/([^/?#]+)/.exec(n.url)?.[1] ?? (n.url.startsWith("/maintenance/work-orders") ? n.work_order_id : null);
    if (mobile && wo) return `/m/work-orders/${wo}`;
    // the phone has no failures screen: a failure/emergency notification opens the WO (or the list with the
    // emergency calls to take)
    if (mobile && /^\/maintenance\/failures/.test(n.url)) return n.work_order_id ? `/m/work-orders/${n.work_order_id}` : "/m/work-orders";
    if (!mobile && /^\/m\/work-orders\/([^/?#]+)/.test(n.url)) return n.url.replace(/^\/m\/work-orders\//, "/maintenance/work-orders/");
    return n.url;
  }
  const woId = n.work_order_id ?? (n.entity_type === "work_order" ? n.entity_id : null);
  if (woId) return mobile ? `/m/work-orders/${woId}` : `/maintenance/work-orders?wo=${woId}`;
  if (n.entity_type === "plan" || /plan/.test(n.type)) return "/maintenance/upcoming";
  if (n.entity_type === "component" || /component/.test(n.type)) return "/maintenance/components";
  if (n.entity_type === "failure" || /failure/.test(n.type)) return n.entity_id ? `/maintenance/failures?f=${n.entity_id}` : "/maintenance/failures";
  if (n.downtime_event_id) return "/stops";
  return mobile ? "/m/work-orders" : "/maintenance/work-orders";
}
