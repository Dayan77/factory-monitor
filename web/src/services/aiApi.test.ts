import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { parseSseFrame, streamChat, type StreamEvent } from "./aiApi";
import { api } from "./api";

function sse(frames: [string, unknown][]): string {
  return frames.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join("");
}

/** A fetch Response whose body arrives in small chunks (frames split across reads). */
function streamResponse(text: string, status = 200): Response {
  const bytes = new TextEncoder().encode(text);
  let pos = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(ctrl) {
      if (pos >= bytes.length) { ctrl.close(); return; }
      ctrl.enqueue(bytes.slice(pos, pos + 17));
      pos += 17;
    },
  });
  return new Response(body, { status, headers: { "Content-Type": "text/event-stream" } });
}

const DONE = {
  reply: "OEE **71,4 %** [1]", citations: [{ id: 1, label: "OEE · Linha", url: "/oee?scope=line" }],
  tool_calls: [{ name: "get_oee", args: {}, status: "ok", rows: 3, duration_ms: 4, summary: "3 turnos" }],
  warnings: [], verified: true, refused: false, refusal_reason: null, conversation_id: "c-1", answer_source: "model",
};

beforeEach(() => { localStorage.setItem("access_token", "jwt-1"); });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("SSE frames", () => {
  it("parses the server events", () => {
    expect(parseSseFrame('event: start\ndata: {"conversation_id": "c"}')).toEqual({ type: "start", conversation_id: "c" });
    expect(parseSseFrame('event: delta\ndata: {"text": "ab"}')).toEqual({ type: "delta", text: "ab" });
    expect(parseSseFrame('event: tool_result\ndata: {"name":"get_oee","status":"ok","rows":2,"summary":"s","citation":{"id":1,"label":"L","url":"/oee"}}'))
      .toEqual({ type: "tool_result", name: "get_oee", status: "ok", rows: 2, summary: "s", citation: { id: 1, label: "L", url: "/oee" } });
    expect(parseSseFrame("event: verifying\ndata: {}")).toEqual({ type: "verifying" });
    expect(parseSseFrame("event: other\ndata: {}")).toBeNull();
    expect(parseSseFrame("event: delta\ndata: {broken")).toBeNull();
  });
});

describe("streamChat", () => {
  it("emits the events in order and resolves with the done payload; sends the bearer token", async () => {
    const fetchMock = vi.fn().mockResolvedValue(streamResponse(sse([
      ["start", { conversation_id: "c-1" }],
      ["tool_call", { name: "get_oee", args: { scope: "line" } }],
      ["tool_result", { name: "get_oee", status: "ok", rows: 3, summary: "3 turnos", citation: { id: 1, label: "OEE · Linha", url: "/oee?scope=line" } }],
      ["verifying", {}],
      ["delta", { text: "OEE **71,4 %** [1]" }],
      ["done", DONE],
    ])));
    vi.stubGlobal("fetch", fetchMock);
    const events: StreamEvent["type"][] = [];
    const r = await streamChat({ message: "OEE?" }, (e) => events.push(e.type));
    expect(events).toEqual(["start", "tool_call", "tool_result", "verifying", "delta", "done"]);
    expect(r.verified).toBe(true);
    expect(r.citations[0].url).toBe("/oee?scope=line");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/ai/chat/stream");
    expect((init as RequestInit).headers).toMatchObject({ Authorization: "Bearer jwt-1" });
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ message: "OEE?" });
  });

  it("falls back to POST /api/ai/chat when the stream closes without `done` (system message)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse("")));
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: { reply: "O assistente de IA está desligado para esta empresa.", answer_source: "system" } });
    const r = await streamChat({ message: "x" }, () => undefined);
    expect(post).toHaveBeenCalledWith("/api/ai/chat", { message: "x" }, expect.anything());
    expect(r.reply).toMatch(/desligado/);
    expect(r.verified).toBe(false);
  });

  it("an error event or HTTP error becomes an exception with the server detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse(sse([["error", { status: 404, detail: "Conversa não encontrada." }]]))));
    await expect(streamChat({ message: "x", conversation_id: "zz" }, () => undefined)).rejects.toThrow("Conversa não encontrada.");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Limite de 20 mensagens por minuto" }), { status: 429 })));
    await expect(streamChat({ message: "x" }, () => undefined)).rejects.toThrow(/20 mensagens/);
  });
});
