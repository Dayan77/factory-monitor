/**
 * AI assistant (Sprint 5 — docs/ai_assistant.md §1, §7). Mirrors server/src/api/routes/ai_chat.py.
 *
 *   POST /api/ai/chat/stream  SSE: start {conversation_id} · tool_call {name, args} · tool_result {name, status, rows,
 *                             summary, citation} · verifying {} · delta {text} · done {AiChatResponse} · error {status, detail}
 *   POST /api/ai/chat         same body → AiChatResponse (used when the stream ends without `done`)
 *   GET/DELETE /api/ai/conversations[/{id}] · GET /api/ai/status
 *
 * The server streams the answer text only after its verifier accepted it (IA-2): nothing unverified reaches the screen.
 */
import { api, authApi } from "./api";

export interface Citation { id: number; label: string; url: string }

export interface ToolCallInfo {
  name: string;
  args: Record<string, unknown>;
  status: string;
  rows?: number | null;
  duration_ms?: number;
  summary?: string;
}

export type RefusalReason = "no_permission" | "insufficient_data" | "out_of_scope" | "write_not_allowed" | "ambiguous" | "budget_exceeded";

export interface AiChatResponse {
  reply: string;
  citations: Citation[];
  tool_calls: ToolCallInfo[];
  warnings: string[];
  verified: boolean;
  refused: boolean;
  refusal_reason: RefusalReason | string | null;
  refusal_reasons?: string[];
  answer_source?: "model" | "template" | "refusal" | "system" | string;
  conversation_id: string | null;
  message_id?: string | null;
  provider?: string | null;
  model?: string | null;
}

export interface AiStatus {
  enabled: boolean;
  configured: boolean;
  provider: string | null;
  model: string | null;
  daily_token_budget: number;
  tokens_used_today: number;
  rate_limit_per_min: number;
  max_tool_calls: number;
}

export interface ConversationSummary { id: string; title: string; created_at: string; updated_at: string; messages: number }

export interface StoredMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  tool_calls: ToolCallInfo[];
  warnings: string[];
  verified: boolean | null;
  refused: boolean | null;
  refusal_reason: string | null;
  created_at: string;
}

export interface Conversation { id: string; title: string; created_at: string; updated_at: string; messages: StoredMessage[] }

export interface ChatContext {
  route?: string;
  plant_id?: string;
  line_id?: string;
  machine_id?: string;
  position_id?: string;
  period?: string;
}

export interface ChatBody { message: string; conversation_id?: string | null; context?: ChatContext }

export type StreamEvent =
  | { type: "start"; conversation_id: string }
  | { type: "tool_call"; name: string; args: Record<string, unknown> }
  | { type: "tool_result"; name: string; status: string; rows: number | null; summary: string; citation: Citation | null }
  | { type: "verifying" }
  | { type: "delta"; text: string }
  | { type: "done"; response: AiChatResponse }
  | { type: "error"; status: number; detail: string };

export const aiApi = {
  status: () => api.get<AiStatus>("/api/ai/status"),
  chat: (body: ChatBody) => api.post<AiChatResponse>("/api/ai/chat", body, { timeout: 60_000 }),
  conversations: () => api.get<ConversationSummary[]>("/api/ai/conversations"),
  conversation: (id: string) => api.get<Conversation>(`/api/ai/conversations/${id}`),
  deleteConversation: (id: string) => api.delete(`/api/ai/conversations/${id}`),
};

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** One SSE frame ("event: x\ndata: {...}") → typed event (unknown events → null). */
export function parseSseFrame(frame: string): StreamEvent | null {
  let event = "message";
  const data: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  let d: unknown = {};
  if (data.length) {
    try { d = JSON.parse(data.join("\n")); } catch { return null; }
  }
  const o = isRecord(d) ? d : {};
  switch (event) {
    case "start": return typeof o.conversation_id === "string" ? { type: "start", conversation_id: o.conversation_id } : null;
    case "tool_call": return { type: "tool_call", name: String(o.name ?? ""), args: isRecord(o.args) ? o.args : {} };
    case "tool_result": {
      const c = isRecord(o.citation) && typeof o.citation.url === "string" ? (o.citation as unknown as Citation) : null;
      return { type: "tool_result", name: String(o.name ?? ""), status: String(o.status ?? ""), rows: typeof o.rows === "number" ? o.rows : null, summary: typeof o.summary === "string" ? o.summary : "", citation: c };
    }
    case "verifying": return { type: "verifying" };
    case "delta": return typeof o.text === "string" ? { type: "delta", text: o.text } : null;
    case "done": return { type: "done", response: normalizeResponse(o) };
    case "error": return { type: "error", status: typeof o.status === "number" ? o.status : 500, detail: typeof o.detail === "string" ? o.detail : "Falha no assistente." };
    default: return null;
  }
}

export function normalizeResponse(o: Record<string, unknown>): AiChatResponse {
  const arr = <T,>(v: unknown) => (Array.isArray(v) ? (v as T[]) : []);
  return {
    reply: typeof o.reply === "string" ? o.reply : "",
    citations: arr<Citation>(o.citations).filter((c) => isRecord(c) && typeof c.url === "string"),
    tool_calls: arr<ToolCallInfo>(o.tool_calls),
    warnings: arr<string>(o.warnings).filter((w) => typeof w === "string"),
    verified: o.verified === true,
    refused: o.refused === true,
    refusal_reason: typeof o.refusal_reason === "string" ? o.refusal_reason : null,
    refusal_reasons: arr<string>(o.refusal_reasons),
    answer_source: typeof o.answer_source === "string" ? o.answer_source : undefined,
    conversation_id: typeof o.conversation_id === "string" ? o.conversation_id : null,
    message_id: typeof o.message_id === "string" ? o.message_id : null,
    provider: typeof o.provider === "string" ? o.provider : null,
    model: typeof o.model === "string" ? o.model : null,
  };
}

export class AiHttpError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function errorDetail(res: Response): Promise<string> {
  try {
    const j: unknown = await res.json();
    if (isRecord(j) && typeof j.detail === "string") return j.detail;
  } catch { /* not json */ }
  return res.status === 429 ? "Muitas mensagens em pouco tempo. Aguarde um pouco." : `O servidor respondeu com erro ${res.status}.`;
}

/**
 * Streams one turn. Calls `onEvent` for each SSE event. Resolves with the final response:
 * the `done` payload, or — when the stream closes without one (e.g. assistant switched off) — the non-stream answer.
 * A 401 refreshes the token once through the API client and retries.
 */
export async function streamChat(body: ChatBody, onEvent: (e: StreamEvent) => void, signal?: AbortSignal): Promise<AiChatResponse> {
  const base = import.meta.env.VITE_API_URL ?? "";
  const post = () => fetch(`${base}/api/ai/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream", Authorization: `Bearer ${localStorage.getItem("access_token") ?? ""}` },
    body: JSON.stringify(body),
    signal,
  });
  let res = await post();
  if (res.status === 401) {
    await authApi.me().catch(() => undefined);      // the axios interceptor rotates the token (or logs out)
    res = await post();
  }
  if (!res.ok || !res.body) throw new AiHttpError(res.status, await errorDetail(res));

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let done: AiChatResponse | null = null;
  let error: StreamEvent | null = null;
  for (;;) {
    const { value, done: end } = await reader.read();
    if (value) buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.search(/\r?\n\r?\n/)) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx).replace(/^\r?\n\r?\n/, "");
      const ev = parseSseFrame(frame);
      if (!ev) continue;
      if (ev.type === "done") done = ev.response;
      if (ev.type === "error") error = ev;
      onEvent(ev);
    }
    if (end) break;
  }
  if (done) return done;
  if (error && error.type === "error") throw new AiHttpError(error.status, error.detail);
  // no `done`: the server answered with a system message (assistant off / not configured) — ask it plainly
  const r = await aiApi.chat(body);
  return normalizeResponse(r.data as unknown as Record<string, unknown>);
}
