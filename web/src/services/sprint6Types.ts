/**
 * Types of the Sprint 6 APIs (docs/sprint6.md, docs/reports.md), mirrored from the server routes:
 * server/src/api/routes/{layout,onboarding,demo,reports,gateways}.py and server/src/services/layout/*.
 */
import type { MachineStatus } from "./types";

// ── plant map (§1) ────────────────────────────────────────────────

/** Visual state of a marker (§1.2, block s6-status-precedence), in precedence order. */
export type VisualState = "offline" | "failure" | "maintenance" | "planned_stop" | "off_shift" | "stopped" | "running";
export type MarkerKind = "machine" | "line_label" | "area_label";
export type MarkerSize = "s" | "m" | "l";
export type LayoutStatus = "draft" | "published" | "archived";

export interface LayoutMarker {
  id?: string;
  kind: MarkerKind;
  machine_id: string | null;
  line_id: string | null;
  label: string | null;
  /** Normalized centre, origin top-left, [0, 1]. */
  x: number;
  y: number;
  rotation_deg: number;
  size: MarkerSize;
}

export interface PlantLayout {
  id: string;
  plant_id: string;
  version: number;
  status: LayoutStatus;
  has_image: boolean;
  image_url: string | null;
  image_content_type: string | null;
  image_bytes: number | null;
  image_width_px: number | null;
  image_height_px: number | null;
  scale_m_per_px: number | null;
  needs_review: boolean;
  created_by: string | null;
  published_by: string | null;
  published_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  markers?: LayoutMarker[];
}

export interface MachineKpis {
  oee: number | null;
  cadence_per_hour: number | null;
  good: number;
  scrap: number;
  reason?: string | null;
}

export interface MarkerBadge {
  kind: "predictive" | string;
  severity?: string;
  alert_id?: string | null;
}

export interface MachineVisual {
  machine_id: string;
  name: string;
  line_id: string;
  line_name: string | null;
  state: VisualState;
  icon: string;
  text: string;
  /** Start of the current state (durations are counted on the client from the server clock). */
  since: string | null;
  detail: {
    fault_code?: number;
    failure_code?: string;
    work_order_id?: string | null;
    until?: string | null;
    next_shift_start?: string | null;
    downtime_id?: string | null;
    reason_code?: string | null;
  };
  badges: MarkerBadge[];
  status: MachineStatus | null;
  last_seen_at: string | null;
  kpis: MachineKpis | null;
}

export interface LineVisual {
  line_id: string;
  name: string;
  output_machine_id: string | null;
  output_state: VisualState | null;
  output_text: string | null;
  kpis: MachineKpis | null;
}

export interface ShiftNow {
  name: string;
  start: string;
  end: string;
  productive_day: string;
}

interface MapViewBase {
  plant_id: string;
  plant_name: string;
  generated_at: string;
  machines: MachineVisual[];
  lines: LineVisual[];
  shift: ShiftNow | null;
  unplaced_machine_ids: string[];
}

export interface PublishedMapView extends MapViewBase {
  kind: "published";
  layout: PlantLayout & { markers: LayoutMarker[] };
}

export interface AutoMapView extends MapViewBase {
  kind: "auto";
  banner: string;
  lanes: { line_id: string; name: string; machine_ids: string[]; y: number }[];
  markers: LayoutMarker[];
}

export type MapView = PublishedMapView | AutoMapView;

// ── onboarding (§4) ───────────────────────────────────────────────

export type StepState = "pending" | "in_progress" | "done";

export interface OnboardingStep {
  step: number;
  key: string;
  title: string;
  state: StepState;
  data: Record<string, unknown>;
  warnings: string[];
  at: string | null;
  by: string | null;
}

export interface OnboardingProgress {
  tenant_id: string;
  plant_id: string | null;
  steps: OnboardingStep[];
  current_step: number | null;
  ready_at: string | null;
  completed: boolean;
}

/** 422 body of PUT /api/onboarding. */
export interface StepErrorDetail {
  message: string;
  errors: string[];
  warnings: string[];
}

export interface ReadinessCheck {
  id: string;
  required: boolean;
  ok: boolean;
  status: "ok" | "fail" | "warn";
  detail: string;
  items: unknown[];
  fix_link: string | null;
}

export interface Readiness {
  plant_id: string;
  checked_at: string;
  ready: boolean;
  window_min: number;
  checks: ReadinessCheck[];
  warnings: string[];
}

export type ImportRowStatus = "ok" | "update" | "error";

export interface ImportRow {
  line: number;
  status: ImportRowStatus;
  message: string;
  /** tags CSV */
  chave?: string;
  /** assets CSV */
  linha?: string;
  maquina?: string;
  posicao?: string | null;
}

export interface ImportPreview {
  rows: ImportRow[];
  summary: Record<ImportRowStatus, number>;
  can_confirm: boolean;
  warnings?: string[];
  delimiter?: string;
  confirmed?: boolean;
  created?: number | Record<string, number>;
  updated?: number;
}

export type BrowseStatus = "pending" | "sent" | "done" | "failed" | "expired";

export interface BrowseNode {
  node_id: string;
  browse_name?: string | null;
  display_name?: string | null;
  node_class?: string | null;
  data_type?: string | null;
  access_level?: number | string | null;
  parent_id?: string | null;
}

export interface BrowseCommand {
  request_id: string;
  id?: string;
  gateway_id?: string;
  status: BrowseStatus;
  error?: string | null;
  expires_at?: string | null;
  result?: { nodes: BrowseNode[]; truncated: boolean; count?: number } | null;
}

// ── demo (§5) ─────────────────────────────────────────────────────

export interface DemoSessionOut {
  access_token: string;
  expires_at: string;
  redirect_to?: string;
  banner?: string | null;
  ai_questions_quota?: number;
  read_only_message?: string;
}

export interface DemoStatus {
  available: boolean;
  plant_running?: boolean;
  banner: string | null;
  message?: string;
  last_data_at?: string | null;
}

export interface DemoSessionInfo {
  id: string;
  expires_at: string;
  ai_questions_used: number;
  ai_questions_quota: number;
  ai_questions_left: number;
  banner?: string;
}

// ── reports (reports.md) ──────────────────────────────────────────

export type ReportCode = "daily_production" | "shift" | "weekly_oee" | "monthly_maintenance" | "reliability_life";
export type ReportFormat = "pdf" | "xlsx";
export type ScopeType = "tenant" | "plant" | "line";

export interface CatalogEntry {
  code: ReportCode;
  name: string;
  title: string;
  question: string;
  scope: ScopeType[];
  period: string;
  formats: ReportFormat[];
  settle_min: number;
  deliver_at_local: string | null;
  weekday: number | null;
  month_day: number | null;
  recipients: string[];
  landscape: boolean;
  screen: string;
  allowed: boolean;
}

export interface GenerateBody {
  code: ReportCode;
  scope_type?: ScopeType;
  scope_id?: string;
  format: ReportFormat;
  params: Record<string, string>;
  observations?: string;
}

export interface ReportRun {
  id: string;
  schedule_id: string | null;
  report_type: ReportCode;
  scope_type: ScopeType;
  scope_id: string | null;
  scope_name: string | null;
  period_key: string;
  period_label: string | null;
  format: ReportFormat;
  version: number;
  status: "generating" | "done" | "failed" | string;
  error: string | null;
  generated_at: string | null;
  data_until: string | null;
  file_size: number | null;
  filename: string | null;
  created_at: string | null;
  download_url: string | null;
}

export interface Recipients {
  roles: string[];
  user_ids: string[];
  external_ids: string[];
}

export interface ReportSchedule {
  id: string;
  name: string | null;
  report_type: ReportCode;
  report_name: string;
  scope_type: ScopeType;
  scope_id: string | null;
  params: Record<string, unknown>;
  formats: ReportFormat[];
  deliver_at_local: string | null;
  weekday: number | null;
  month_day: number | null;
  enabled: boolean;
  paused_at: string | null;
  recipients: Recipients;
  next_send_at: string | null;
  mail_configured: boolean;
  warning: string | null;
  created_at?: string | null;
}

export interface ScheduleBody {
  report_type: ReportCode;
  name?: string;
  scope_type?: ScopeType;
  scope_id?: string;
  formats?: ReportFormat[];
  deliver_at_local?: string;
  weekday?: number;
  month_day?: number;
  enabled?: boolean;
  recipients?: Recipients;
  params?: Record<string, unknown>;
}

export interface Dispatch {
  id: string;
  period_key: string;
  period_label: string | null;
  ready_at: string | null;
  send_at: string | null;
  status: string;
  attempts: number;
  last_error: string | null;
  manual: boolean;
  completed_at: string | null;
}

export interface Delivery {
  id: string;
  dispatch_id: string | null;
  email: string | null;
  status: string;
  channel: string | null;
  link_only: boolean;
  error: string | null;
  sent_at: string | null;
}

export interface ScheduleHistory {
  schedule: ReportSchedule;
  dispatches: Dispatch[];
  runs: ReportRun[];
  deliveries: Delivery[];
}

export interface ReportsStatus {
  mail_channel: string | null;
  mail_configured: boolean;
  warning: string | null;
}
