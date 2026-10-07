/** Expiry (ms epoch) of a JWT, read without verifying it (only to schedule a renewal); null when unreadable. */
export function tokenExpiryMs(token: string | null | undefined): number | null {
  if (!token) return null;
  try {
    const part = token.split(".")[1];
    if (!part) return null;
    const json = atob(part.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(part.length / 4) * 4, "="));
    const exp = (JSON.parse(json) as { exp?: unknown }).exp;
    return typeof exp === "number" && Number.isFinite(exp) ? exp * 1000 : null;
  } catch {
    return null;
  }
}

/** Fired on window (detail = new access token) whenever this tab stores a renewed token pair. */
export const TOKENS_EVENT = "fm-tokens";
