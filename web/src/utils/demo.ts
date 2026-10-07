/**
 * Public read-only DEMO (docs/sprint6.md §5). A demo session is a 2 h JWT with role `demo_viewer` in the demo tenant:
 * every write answers 403 "Modo demonstração: alterações desativadas" on the server; the UI disables the write
 * buttons with the same text and hides administration, users, gateways, settings and cameras.
 */
import { rolesOf, type RoleCarrier } from "./maintenance";

export const DEMO_ROLE = "demo_viewer";
export const DEMO_WRITE_MSG = "Modo demonstração: alterações desativadas";
export const DEMO_BANNER = "Demonstração — dados de uma planta simulada (planta virtual FactoryConnect)";
export const DEMO_AREA_MSG = "Modo demonstração: área indisponível (administração, usuários, gateways, configurações e câmeras)";
/** sessionStorage flag set when the visitor came from "Ver demonstração" (before /auth/me answers). */
export const DEMO_FLAG = "fm-demo";

export function isDemoUser(u: RoleCarrier | null | undefined): boolean {
  return rolesOf(u).includes(DEMO_ROLE);
}

/** Screens a demo visitor never sees (navigation hidden, route answers "área indisponível"). */
export const DEMO_BLOCKED_PREFIXES = [
  "/admin", "/settings", "/cameras", "/onboarding", "/reports/scheduled", "/m", "/op",
];

export function isDemoBlockedPath(pathname: string): boolean {
  return DEMO_BLOCKED_PREFIXES.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

/** Writes the server lets a demo visitor make: only the AI assistant questions (with a per-session quota). */
const DEMO_ALLOWED_WRITES = ["/api/ai/chat", "/api/ai/chat/stream"];

export function demoAllowsRequest(method: string | undefined, url: string | undefined): boolean {
  const m = (method ?? "get").toUpperCase();
  if (m === "GET" || m === "HEAD" || m === "OPTIONS") return true;
  const path = (url ?? "").replace(/^https?:\/\/[^/]+/, "").split("?")[0].replace(/\/+$/, "");
  return DEMO_ALLOWED_WRITES.includes(path);
}

/** Is the stored access token a demo token? (role claim of the JWT, decoded without verification — UI only). */
export function tokenIsDemo(token: string | null): boolean {
  if (!token) return false;
  const part = token.split(".")[1];
  if (!part) return false;
  try {
    const json = JSON.parse(atob(part.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(part.length / 4) * 4, "="))) as { role?: unknown };
    return json.role === DEMO_ROLE;
  } catch {
    return false;
  }
}
