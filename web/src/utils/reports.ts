/**
 * Pure helpers of the report screens (docs/reports.md): export params per screen, schedule form validation,
 * download filenames and labels.
 */
import { isAxiosError } from "axios";
import type { CatalogEntry, ReportCode, ReportFormat, ScheduleBody, ScopeType } from "@/services/sprint6Types";

export const REPORT_LABEL: Record<ReportCode, string> = {
  daily_production: "Produção diária",
  shift: "Relatório de turno",
  weekly_oee: "Revisão semanal de OEE",
  monthly_maintenance: "Gestão da manutenção (mensal)",
  reliability_life: "Confiabilidade e vida (Weibull)",
};

export const FORMAT_LABEL: Record<ReportFormat, string> = { pdf: "PDF", xlsx: "Excel" };

export const WEEKDAY_LABEL = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"];

export const ROLE_LABEL: Record<string, string> = {
  admin: "Administrador",
  manager: "Gestor",
  production_supervisor: "Supervisor de produção",
  maintenance_planner: "Planejador de manutenção",
  technician: "Técnico",
  operator: "Operador",
};

/** "filename" of a Content-Disposition header (RFC 6266, also filename*=UTF-8''…). */
export function filenameFrom(header: string | null | undefined, fallback: string): string {
  if (!header) return fallback;
  const star = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (star) {
    try { return decodeURIComponent(star[1].trim().replace(/^"|"$/g, "")); } catch { /* fall through */ }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain ? plain[1].trim() : fallback;
}

/** Monday (YYYY-MM-DD) of the week containing `day`. */
export function weekStart(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  const t = new Date(Date.UTC(y, m - 1, d));
  const wd = (t.getUTCDay() + 6) % 7;
  t.setUTCDate(t.getUTCDate() - wd);
  return t.toISOString().slice(0, 10);
}

export function addDaysIso(day: string, n: number): string {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}

/** Message of an API error whose body came back as a Blob (responseType: "blob"). */
export async function blobErrorMessage(err: unknown, fallback: string): Promise<string> {
  if (!isAxiosError(err)) return fallback;
  const data: unknown = err.response?.data;
  if (typeof Blob !== "undefined" && data instanceof Blob) {
    try {
      const j = JSON.parse(await data.text()) as { detail?: unknown };
      if (typeof j.detail === "string") return j.detail;
      if (j.detail && typeof j.detail === "object" && "message" in j.detail) return String((j.detail as { message: unknown }).message);
    } catch { /* not JSON */ }
  }
  if (data && typeof data === "object" && "detail" in data && typeof (data as { detail: unknown }).detail === "string") {
    return (data as { detail: string }).detail;
  }
  if (err.response?.status === 403) return "Permissão insuficiente para este relatório.";
  return fallback;
}

/** Saves a Blob as a file in the browser. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

// ── schedule form ─────────────────────────────────────────────────

export interface ScheduleForm {
  report_type: ReportCode | "";
  name: string;
  scope_type: ScopeType | "";
  scope_id: string;
  formats: ReportFormat[];
  deliver_at_local: string;
  weekday: string;
  month_day: string;
  roles: string[];
}

export function emptyScheduleForm(entry?: CatalogEntry): ScheduleForm {
  return {
    report_type: entry?.code ?? "",
    name: "",
    scope_type: entry?.scope[0] ?? "",
    scope_id: "",
    formats: entry ? [...entry.formats] : ["pdf"],
    deliver_at_local: entry?.deliver_at_local ?? "07:00",
    weekday: entry?.weekday != null ? String(entry.weekday) : "0",
    month_day: entry?.month_day != null ? String(entry.month_day) : "1",
    roles: entry ? [...entry.recipients] : [],
  };
}

const HHMM = /^([01]\d|2[0-3]):([0-5]\d)$/;

/** Field errors of the schedule form (same rules as the server's ScheduleIn). */
export function validateSchedule(f: ScheduleForm, entry: CatalogEntry | undefined): Record<string, string> {
  const e: Record<string, string> = {};
  if (!f.report_type || !entry) {
    e.report_type = "Escolha o relatório.";
    return e;
  }
  if (!f.scope_type || !entry.scope.includes(f.scope_type)) e.scope_type = "Escopo inválido para este relatório.";
  if (f.scope_type && f.scope_type !== "tenant" && !f.scope_id) e.scope_id = f.scope_type === "line" ? "Escolha a linha." : "Escolha a planta.";
  if (f.formats.length === 0) e.formats = "Marque ao menos um formato.";
  if (f.formats.some((x) => !entry.formats.includes(x))) e.formats = `Formatos disponíveis: ${entry.formats.map((x) => FORMAT_LABEL[x]).join(", ")}.`;
  if (entry.code !== "shift" && !HHMM.test(f.deliver_at_local)) e.deliver_at_local = "Horário no formato HH:MM.";
  if (entry.period === "week") {
    const w = Number(f.weekday);
    if (!Number.isInteger(w) || w < 0 || w > 6) e.weekday = "Dia da semana inválido.";
  }
  if (entry.period === "month") {
    const d = Number(f.month_day);
    if (!Number.isInteger(d) || d < 1 || d > 28) e.month_day = "Dia do mês entre 1 e 28.";
  }
  if (f.roles.length === 0) e.roles = "Escolha ao menos um papel destinatário.";
  return e;
}

export function scheduleBody(f: ScheduleForm, entry: CatalogEntry): ScheduleBody {
  const body: ScheduleBody = {
    report_type: entry.code,
    scope_type: f.scope_type || undefined,
    formats: f.formats,
    recipients: { roles: f.roles, user_ids: [], external_ids: [] },
  };
  if (f.name.trim()) body.name = f.name.trim();
  if (f.scope_type && f.scope_type !== "tenant") body.scope_id = f.scope_id;
  if (entry.code !== "shift") body.deliver_at_local = f.deliver_at_local;
  if (entry.period === "week") body.weekday = Number(f.weekday);
  if (entry.period === "month") body.month_day = Number(f.month_day);
  return body;
}

/** "toda segunda às 07:00", "todo dia às 06:30", "ao fim de cada turno + 15 min". */
export function cadenceLabel(s: { report_type: ReportCode; deliver_at_local: string | null; weekday: number | null; month_day: number | null }): string {
  const at = s.deliver_at_local ? ` às ${s.deliver_at_local}` : "";
  switch (s.report_type) {
    case "shift": return "ao fim de cada turno + 15 min";
    case "daily_production": return `todo dia${at}`;
    case "weekly_oee": return `toda ${WEEKDAY_LABEL[s.weekday ?? 0]}${at}`;
    case "monthly_maintenance": return `todo dia ${s.month_day ?? 1}${at}`;
    case "reliability_life": return `1º dia de cada trimestre${at}`;
  }
}

export const DISPATCH_STATUS_LABEL: Record<string, string> = {
  pending: "Pendente",
  generating: "Gerando",
  sent: "Enviado",
  done: "Concluído",
  failed: "Falhou",
  retrying: "Nova tentativa",
  skipped: "Sem destinatários",
  stored: "Só para download",
  download_only: "Só para download",
  no_channel: "E-mail não configurado (só download)",
  bounced: "Devolvido",
  unsubscribed: "Descadastrado",
};
