import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AiChatPage from "./AiChatPage";
import { aiApi, streamChat, type AiChatResponse, type StreamEvent } from "@/services/aiApi";
import { plantsApi } from "@/services/api";
import { useAuthStore } from "@/store/auth";
import { plantTree } from "@/test/fixtures";
import { TECH, user } from "@/test/maintenanceFixtures";
import { ok, renderRoute } from "@/test/utils";

vi.mock("@/services/api", () => ({ plantsApi: { tree: vi.fn() }, authApi: { me: vi.fn() } }));
vi.mock("@/services/aiApi", async (orig) => {
  const real = await orig<typeof import("@/services/aiApi")>();
  return {
    ...real,
    aiApi: { status: vi.fn(), chat: vi.fn(), conversations: vi.fn(), conversation: vi.fn(), deleteConversation: vi.fn() },
    streamChat: vi.fn(),
  };
});

const STATUS = { enabled: true, configured: true, provider: "scripted", model: "scripted-demo", daily_token_budget: 0, tokens_used_today: 0, rate_limit_per_min: 20, max_tool_calls: 6 };
const RESPONSE: AiChatResponse = {
  reply: "OEE da **Linha de Montagem** ontem: **71,4 %** [1].\n\n- Turno A: 75,0 % [1]\n\nOnde ver: [OEE](/oee?scope=line&id=line-1)",
  citations: [{ id: 1, label: "OEE · Linha de Montagem · 05/10 por turno", url: "/oee?scope=line&id=line-1&group_by=shift" }],
  tool_calls: [{ name: "get_oee", args: {}, status: "ok", rows: 3, summary: "3 turnos" }],
  warnings: ["Dados incompletos no turno C (cobertura 91,0 %)."], verified: true, refused: false, refusal_reason: null,
  conversation_id: "c-1", answer_source: "model",
};

beforeEach(() => {
  useAuthStore.setState({ user: user({ role: "manager", roles: ["manager"] }), loading: false, offline: false });
  vi.mocked(plantsApi.tree).mockResolvedValue(ok(plantTree()));
  vi.mocked(aiApi.status).mockResolvedValue(ok(STATUS));
  vi.mocked(aiApi.conversations).mockResolvedValue(ok([]));
});

describe("Assistente de IA", () => {
  it("not configured → clear empty state, no composer", async () => {
    vi.mocked(aiApi.status).mockResolvedValue(ok({ ...STATUS, configured: false, provider: null, model: null }));
    renderRoute(<AiChatPage />, "/ai", "/ai");
    expect(await screen.findByTestId("ai-not-configured")).toHaveTextContent("ainda não está configurado");
    expect(screen.queryByLabelText("Pergunta")).toBeNull();
  });

  it("suggested questions per role (technician)", async () => {
    useAuthStore.setState({ user: TECH });
    renderRoute(<AiChatPage />, "/ai", "/ai");
    const s = await screen.findByTestId("suggestions");
    expect(within(s).getAllByRole("button")[0]).toHaveTextContent("Quais são as minhas OS de hoje?");
  });

  it("streams: shows 'consultando' steps, then the verified answer with citations linking to the screen", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    vi.mocked(streamChat).mockImplementation(async (_body, on: (e: StreamEvent) => void) => {
      on({ type: "start", conversation_id: "c-1" });
      on({ type: "tool_call", name: "get_oee", args: { scope: "line" } });
      await gate;
      on({ type: "tool_result", name: "get_oee", status: "ok", rows: 3, summary: "3 turnos", citation: RESPONSE.citations[0] });
      on({ type: "verifying" });
      on({ type: "delta", text: RESPONSE.reply });
      return RESPONSE;
    });
    const u = userEvent.setup();
    renderRoute(<AiChatPage />, "/ai", "/ai");
    await u.type(await screen.findByLabelText("Pergunta"), "Como foi o OEE ontem?{Enter}");
    expect(await screen.findByText("Consultando o OEE…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Parar" })).toBeInTheDocument();
    release();
    const msg = await screen.findByTestId("assistant-message");
    await waitFor(() => expect(within(msg).getByTestId("answer-sources")).toBeInTheDocument());
    const ref = within(msg).getAllByTestId("citation-ref")[0];
    expect(ref).toHaveAttribute("href", "/oee?scope=line&id=line-1&group_by=shift");
    expect(within(msg).getByRole("link", { name: "OEE" })).toHaveAttribute("href", "/oee?scope=line&id=line-1");
    expect(within(msg).getByTestId("answer-warnings")).toHaveTextContent("cobertura 91,0 %");
    expect(msg).toHaveTextContent("Números conferidos com os dados");
    expect(within(msg).getByRole("button", { name: "Copiar" })).toBeInTheDocument();
    expect(vi.mocked(streamChat).mock.calls[0][0]).toMatchObject({ message: "Como foi o OEE ontem?", conversation_id: null, context: { route: "/ai", plant_id: "plant-1" } });
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/ai?c=c-1"));
  });

  it("refusal is shown as such (write_not_allowed)", async () => {
    vi.mocked(streamChat).mockResolvedValue({ ...RESPONSE, reply: "Não posso abrir OS. Use [Nova OS](/maintenance/work-orders?new=1).", citations: [], warnings: [], refused: true, refusal_reason: "write_not_allowed", refusal_reasons: ["write_not_allowed"], answer_source: "refusal" });
    const u = userEvent.setup();
    renderRoute(<AiChatPage />, "/ai", "/ai");
    await u.type(await screen.findByLabelText("Pergunta"), "Abra uma OS{Enter}");
    expect(await screen.findByTestId("refusal")).toHaveTextContent("Ação não permitida pelo assistente");
    expect(screen.getByRole("link", { name: "Nova OS" })).toHaveAttribute("href", "/maintenance/work-orders?new=1");
  });

  it("errors are explicit (429)", async () => {
    const { AiHttpError } = await import("@/services/aiApi");
    vi.mocked(streamChat).mockRejectedValue(new AiHttpError(429, "Limite de 20 mensagens por minuto do assistente atingido."));
    const u = userEvent.setup();
    renderRoute(<AiChatPage />, "/ai", "/ai");
    await u.type(await screen.findByLabelText("Pergunta"), "oi{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Limite de 20 mensagens");
  });

  it("history: opens a stored conversation and deletes one", async () => {
    vi.mocked(aiApi.conversations).mockResolvedValue(ok([{ id: "c-9", title: "OEE de ontem", created_at: "2026-10-05T10:00:00Z", updated_at: "2026-10-05T10:01:00Z", messages: 2 }]));
    vi.mocked(aiApi.conversation).mockResolvedValue(ok({ id: "c-9", title: "OEE de ontem", created_at: "", updated_at: "", messages: [
      { id: "m1", role: "user", content: "OEE de ontem?", citations: [], tool_calls: [], warnings: [], verified: null, refused: null, refusal_reason: null, created_at: "" },
      { id: "m2", role: "assistant", content: "**71,4 %** [1]", citations: RESPONSE.citations, tool_calls: RESPONSE.tool_calls, warnings: [], verified: true, refused: false, refusal_reason: null, created_at: "" },
    ] }));
    vi.mocked(aiApi.deleteConversation).mockResolvedValue(ok(undefined));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const u = userEvent.setup();
    renderRoute(<AiChatPage />, "/ai", "/ai");
    const list = (await screen.findAllByTestId("conversation-list"))[0];
    await u.click(within(list).getByRole("button", { name: /^OEE de ontem/ }));
    expect(await screen.findByTestId("user-message")).toHaveTextContent("OEE de ontem?");
    expect(screen.getByTestId("citation-ref")).toHaveAttribute("href", RESPONSE.citations[0].url);
    await u.click(within(list).getByRole("button", { name: "Excluir conversa OEE de ontem" }));
    await waitFor(() => expect(aiApi.deleteConversation).toHaveBeenCalledWith("c-9"));
  });
});
