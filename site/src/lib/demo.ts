// "Ver demonstração" (docs/sprint6.md §5). Módulo puro (sem DOM) para ser testado com vitest.
//
// Fluxo escolhido (o mais seguro): o site NUNCA recebe nem repassa o token da demo.
//   1. clique → o site consulta GET {PUBLIC_API_URL}/api/public/demo-status (sem token, sem gastar a cota de
//      sessões por IP) só para mostrar carregando/erro aqui mesmo;
//   2. disponível → registra o evento "abriu demo" (anônimo, ver trackDemoOpened) e navega para {APP_URL}/demo;
//   3. a rota /demo do app (mesma origem da API) faz POST /api/public/demo-session, guarda o JWT de 2 h no próprio
//      armazenamento e vai para /dashboard.
// Assim o JWT não aparece em URL (histórico, logs, Referer), não atravessa origens e não fica no site.
// Sem JavaScript o link funciona igual (href = {APP_URL}/demo).

export type DemoCheck =
  | { kind: "ok"; plantRunning: boolean }
  | { kind: "unavailable"; message: string }
  | { kind: "error"; message: string };

export const MSG_UNAVAILABLE = "A demonstração está indisponível no momento. Tente de novo em alguns minutos.";
export const MSG_ERROR = "Não foi possível abrir a demonstração agora. Verifique a conexão e tente de novo.";
export const MSG_LOADING = "Abrindo demonstração…";

const trim = (u: string) => u.replace(/\/+$/, "");

export function demoUrls(appUrl: string, apiUrl: string) {
  return { app: `${trim(appUrl)}/demo`, status: `${trim(apiUrl)}/api/public/demo-status` };
}

type FetchLike = (url: string, init?: { method?: string; signal?: AbortSignal; headers?: Record<string, string>;
                                        credentials?: "omit" | "same-origin" | "include"; cache?: "no-store" }) =>
  Promise<{ ok: boolean; status: number; json: () => Promise<unknown> }>;

/** Consulta a disponibilidade da demo. Nunca lança: erro de rede, timeout ou resposta inesperada → `error`. */
export async function checkDemo(statusUrl: string, fetchImpl: FetchLike, timeoutMs = 8000): Promise<DemoCheck> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetchImpl(statusUrl, { method: "GET", signal: ctrl.signal, credentials: "omit", cache: "no-store",
                                             headers: { Accept: "application/json" } });
    if (res.status === 503 || res.status === 404) return { kind: "unavailable", message: MSG_UNAVAILABLE };
    if (!res.ok) return { kind: "error", message: MSG_ERROR };
    const body = (await res.json()) as { available?: unknown; plant_running?: unknown } | null;
    if (!body || typeof body !== "object" || typeof body.available !== "boolean") return { kind: "error", message: MSG_ERROR };
    if (!body.available) return { kind: "unavailable", message: MSG_UNAVAILABLE };
    return { kind: "ok", plantRunning: body.plant_running === true };
  } catch {
    return { kind: "error", message: MSG_ERROR };
  } finally {
    clearTimeout(timer);
  }
}

export interface PrivacySignals {
  doNotTrack?: string | null;
  globalPrivacyControl?: boolean;
}

/** LGPD: respeita "Do Not Track" e Global Privacy Control — com qualquer um ligado, nenhum evento sai. */
export function trackingAllowed(nav: PrivacySignals | undefined): boolean {
  if (!nav) return false;
  if (nav.doNotTrack === "1" || nav.doNotTrack === "yes") return false;
  if (nav.globalPrivacyControl === true) return false;
  return true;
}

/** Corpo do evento: só o nome e a página de origem. Sem cookie, IP, id, user agent ou horário do cliente. */
export function demoEvent(placement: string): { event: "abriu_demo"; placement: string } {
  const p = /^[a-z0-9-]{1,32}$/.test(placement) ? placement : "outro";
  return { event: "abriu_demo", placement: p };
}

/**
 * Evento único rastreado pelo site: "abriu demo". Vai por `sendBeacon` (sem cookies) para o endpoint first-party
 * configurado em PUBLIC_DEMO_EVENT_URL; sem endpoint configurado, nada sai do navegador (o servidor já conta as
 * sessões de demonstração abertas, sem IP — docs/sprint6.md §5). Retorna se o evento foi enviado.
 */
export function trackDemoOpened(endpoint: string, placement: string, nav: (PrivacySignals & {
  sendBeacon?: (url: string, data: string | Blob) => boolean }) | undefined): boolean {
  if (!endpoint || !trackingAllowed(nav) || typeof nav?.sendBeacon !== "function") return false;
  try {
    return nav.sendBeacon(endpoint, JSON.stringify(demoEvent(placement)));
  } catch {
    return false;
  }
}
