/**
 * Line templates of the onboarding (sprint "onboarding sem passos manuais", C3). Mirrors
 * GET /api/onboarding/line-templates and POST /api/onboarding/line-templates/{key}/apply (admin).
 * Applying is idempotent on the server (matches line / machines / tags by name).
 */
import { api } from "./api";

export interface LineTemplateDevice {
  id: string;
  label: string;
  default_base_url: string;
}

export interface LineTemplate {
  key: string;
  name: string;
  description?: string | null;
  machines: string[];
  devices: LineTemplateDevice[];
}

export interface ApplyLineTemplateBody {
  plant_id: string;
  line_name?: string;
  gateway_id?: string | null;
  devices?: Record<string, { base_url: string }>;
}

export interface ApplyLineTemplateResult {
  line_id: string;
  machines: { id: string; name: string }[];
  tags_created: number;
  tags_updated: number;
  gateway: { id: string; devices: number | unknown[]; tags_assigned: number } | null;
  warnings: string[];
}

export const lineTemplatesApi = {
  list: () => api.get<LineTemplate[]>("/api/onboarding/line-templates"),
  apply: (key: string, body: ApplyLineTemplateBody) =>
    api.post<ApplyLineTemplateResult>(`/api/onboarding/line-templates/${encodeURIComponent(key)}/apply`, body, { timeout: 60_000 }),
};

/** Number of gateway devices in the apply result (the server may send the count or the list of ids). */
export function deviceCount(d: number | unknown[] | undefined | null): number {
  if (typeof d === "number") return d;
  return Array.isArray(d) ? d.length : 0;
}
