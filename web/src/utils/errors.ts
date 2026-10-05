import { isAxiosError } from "axios";

/**
 * Human-readable message from an API error. Understands FastAPI's
 * `{detail: string}` and validation `{detail: [{msg}]}` shapes.
 */
export function apiErrorMessage(err: unknown, fallback: string): string {
  if (!isAxiosError(err)) return fallback;
  const data: unknown = err.response?.data;
  if (typeof data !== "object" || data === null || !("detail" in data)) return fallback;
  const detail: unknown = data.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d: unknown) => (typeof d === "object" && d !== null && "msg" in d && typeof d.msg === "string" ? d.msg : null))
      .filter((m): m is string => m !== null);
    if (msgs.length) return msgs.join(", ");
  }
  return fallback;
}
