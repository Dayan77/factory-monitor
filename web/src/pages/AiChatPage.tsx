import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Card } from "@/components/ui/Card";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import EmptyState from "@/components/ui/EmptyState";
import ErrorState from "@/components/ui/ErrorState";
import Sheet from "@/components/ui/Sheet";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { AnswerFooter, Markdown, ToolSteps, type Step } from "@/components/ai/AnswerView";
import {
  AiHttpError, aiApi, streamChat, type AiChatResponse, type ConversationSummary, type StoredMessage,
} from "@/services/aiApi";
import { usePlant } from "@/hooks/useEfficiency";
import { useAuthStore } from "@/store/auth";
import { rolesOf } from "@/utils/maintenance";
import { suggestionsFor } from "@/utils/aiFormat";
import { formatDateTime } from "@/utils/format";
import { apiErrorMessage } from "@/utils/errors";

interface UiMessage {
  key: string;
  role: "user" | "assistant";
  text: string;
  status: "streaming" | "done" | "error";
  steps: Step[];
  verifying?: boolean;
  response?: AiChatResponse;
  error?: string;
}

let seq = 0;
const nextKey = () => `m${++seq}`;

const aiKeys = { status: ["ai", "status"] as const, conversations: ["ai", "conversations"] as const, conversation: (id: string) => ["ai", "conversation", id] as const };

function fromStored(m: StoredMessage): UiMessage {
  if (m.role === "user") return { key: m.id, role: "user", text: m.content, status: "done", steps: [] };
  return {
    key: m.id, role: "assistant", text: m.content, status: "done",
    steps: (m.tool_calls ?? []).map((t) => ({ name: t.name, args: t.args ?? {}, status: t.status, summary: t.summary, rows: t.rows })),
    response: {
      reply: m.content, citations: m.citations ?? [], tool_calls: m.tool_calls ?? [], warnings: m.warnings ?? [],
      verified: m.verified === true, refused: m.refused === true, refusal_reason: m.refusal_reason, conversation_id: null,
    },
  };
}

function History({ list, current, onOpen, onNew, onDelete }: {
  list: ConversationSummary[] | undefined; current: string | null; onOpen: (id: string) => void; onNew: () => void; onDelete: (c: ConversationSummary) => void;
}) {
  return (
    <div className="flex h-full flex-col gap-2">
      <Button variant="primary" onClick={onNew}>Nova conversa</Button>
      <nav aria-label="Conversas anteriores" className="-mx-1 flex-1 overflow-y-auto px-1">
        {list === undefined ? <Skeleton className="h-24" /> : list.length === 0 ? (
          <p className="px-2 py-4 text-caption text-fg-subtle">Nenhuma conversa ainda. As conversas ficam guardadas por 90 dias e só você as vê.</p>
        ) : (
          <ul className="space-y-0.5" data-testid="conversation-list">
            {list.map((c) => (
              <li key={c.id} className={clsx("group flex items-center gap-1 rounded-lg", c.id === current ? "bg-accent/12 bg-surface-2" : "hover:bg-surface-2/60")}>
                <button type="button" onClick={() => onOpen(c.id)} aria-current={c.id === current ? "true" : undefined} className="min-w-0 flex-1 px-2 py-2 text-left">
                  <span className="block truncate text-body text-fg">{c.title || "Conversa"}</span>
                  <span className="block text-caption text-fg-subtle">{formatDateTime(c.updated_at)}</span>
                </button>
                <button type="button" onClick={() => onDelete(c)} aria-label={`Excluir conversa ${c.title}`}
                  className="mr-1 flex size-8 shrink-0 items-center justify-center rounded-md text-fg-subtle opacity-70 hover:bg-danger-solid/10 hover:text-danger group-hover:opacity-100">
                  <svg viewBox="0 0 20 20" fill="currentColor" className="size-4" aria-hidden="true"><path d="M7 3h6l1 2h3v2H3V5h3l1-2zm-2 5h10l-1 9H6L5 8z" /></svg>
                </button>
              </li>
            ))}
          </ul>
        )}
      </nav>
    </div>
  );
}

function NotConfigured({ disabled }: { disabled: boolean }) {
  return (
    <Card className="mx-auto max-w-2xl" data-testid="ai-not-configured">
      <EmptyState
        icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} className="size-10"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /><path d="M9 9l6 6M15 9l-6 6" /></svg>}
        title={disabled ? "O assistente de IA está desligado para esta empresa" : "O assistente de IA ainda não está configurado"}
        description={disabled
          ? "O administrador desligou o assistente (AI_ENABLED / AI_DISABLED_TENANTS). Os dados continuam disponíveis nas telas de OEE, Paradas e Manutenção."
          : "Para ativar, o administrador do servidor define ANTHROPIC_API_KEY ou OPENAI_API_KEY (e, se quiser, AI_PROVIDER e AI_MODEL) no arquivo .env. Sem isso o assistente não responde — ele nunca inventa números."}
      />
    </Card>
  );
}

/** "Assistente de IA": answers only with the tenant's real data via role-scoped tools (docs/ai_assistant.md). */
export default function AiChatPage() {
  const [params, setParams] = useSearchParams();
  const conversationId = params.get("c");
  const user = useAuthStore((s) => s.user);
  const { plant } = usePlant();
  const qc = useQueryClient();
  const status = useQuery({ queryKey: aiKeys.status, queryFn: () => aiApi.status().then((r) => r.data), staleTime: 60_000 });
  const ready = !!status.data && status.data.configured && status.data.enabled;
  const conversations = useQuery({ queryKey: aiKeys.conversations, queryFn: () => aiApi.conversations().then((r) => r.data), enabled: ready });
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [loadedId, setLoadedId] = useState<string | null>(null);
  const [input, setInput] = useState(params.get("q") ?? "");
  const [busy, setBusy] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  /** Bumped when the conversation on screen changes: late stream events of an abandoned turn are dropped. */
  const genRef = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const suggestions = useMemo(() => suggestionsFor(rolesOf(user)), [user]);

  const conv = useQuery({
    queryKey: aiKeys.conversation(conversationId ?? ""),
    queryFn: () => aiApi.conversation(conversationId as string).then((r) => r.data),
    enabled: ready && !!conversationId && conversationId !== loadedId,
  });
  useEffect(() => {
    if (conv.data && conv.data.id === conversationId && loadedId !== conversationId) {
      setMessages(conv.data.messages.map(fromStored));
      setLoadedId(conv.data.id);
    }
  }, [conv.data, conversationId, loadedId]);
  useEffect(() => {
    if (!conversationId) { setLoadedId(null); }
  }, [conversationId]);

  useEffect(() => { bottomRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" }); }, [messages]);
  useEffect(() => () => abortRef.current?.abort(), []);

  const del = useMutation({
    mutationFn: (id: string) => aiApi.deleteConversation(id),
    onSuccess: (_r, id) => {
      void qc.invalidateQueries({ queryKey: aiKeys.conversations });
      if (id === conversationId) newConversation();
    },
  });

  function newConversation() {
    genRef.current += 1;
    abortRef.current?.abort();
    setMessages([]);
    setLoadedId(null);
    const next = new URLSearchParams(params);
    next.delete("c");
    next.delete("q");
    setParams(next, { replace: true });
    setHistoryOpen(false);
    setTimeout(() => inputRef.current?.focus(), 0);
  }

  function openConversation(id: string) {
    setHistoryOpen(false);
    if (id === conversationId) return;
    genRef.current += 1;
    if (busy) abortRef.current?.abort();
    setLoadedId(null);
    setMessages([]);
    const next = new URLSearchParams(params);
    next.set("c", id);
    next.delete("q");
    setParams(next);
  }

  function patchLast(fn: (m: UiMessage) => UiMessage, gen = genRef.current) {
    if (gen !== genRef.current) return;
    setMessages((prev) => prev.length ? [...prev.slice(0, -1), fn(prev[prev.length - 1])] : prev);
  }

  async function send(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    setInput("");
    setBusy(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const gen = genRef.current;
    const patch = (fn: (m: UiMessage) => UiMessage) => patchLast(fn, gen);
    setMessages((prev) => [...prev, { key: nextKey(), role: "user", text: q, status: "done", steps: [] },
      { key: nextKey(), role: "assistant", text: "", status: "streaming", steps: [] }]);
    let convId = conversationId;
    try {
      const res = await streamChat(
        { message: q, conversation_id: conversationId, context: { route: "/ai", ...(plant ? { plant_id: plant.id } : {}) } },
        (ev) => {
          if (ev.type === "start") convId = ev.conversation_id;
          else if (ev.type === "tool_call") patch((m) => ({ ...m, steps: [...m.steps, { name: ev.name, args: ev.args, status: "running" }] }));
          else if (ev.type === "tool_result") patch((m) => {
            const steps = m.steps.slice();
            const idx = steps.map((s) => s.name === ev.name && s.status === "running").lastIndexOf(true);
            const s = { name: ev.name, args: idx >= 0 ? steps[idx].args : {}, status: ev.status, summary: ev.summary, rows: ev.rows };
            if (idx >= 0) steps[idx] = s; else steps.push(s);
            return { ...m, steps };
          });
          else if (ev.type === "verifying") patch((m) => ({ ...m, verifying: true }));
          else if (ev.type === "delta") patch((m) => ({ ...m, verifying: false, text: m.text + ev.text }));
        },
        ctrl.signal,
      );
      patch((m) => ({ ...m, text: res.reply, status: "done", verifying: false, response: res,
        steps: res.tool_calls.length ? res.tool_calls.map((t) => ({ name: t.name, args: t.args ?? {}, status: t.status, summary: t.summary, rows: t.rows })) : m.steps }));
      convId = res.conversation_id ?? convId;
      if (gen === genRef.current && convId && convId !== conversationId) {
        setLoadedId(convId);
        const next = new URLSearchParams(params);
        next.set("c", convId);
        next.delete("q");
        setParams(next, { replace: true });
      }
      void qc.invalidateQueries({ queryKey: aiKeys.conversations });
    } catch (e) {
      if (ctrl.signal.aborted) patch((m) => ({ ...m, status: "error", error: "Resposta interrompida." }));
      else {
        const msg = e instanceof AiHttpError ? e.message : e instanceof TypeError ? "Sem conexão com o servidor." : apiErrorMessage(e, "Falha ao consultar o assistente.");
        patch((m) => ({ ...m, status: "error", error: msg }));
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send(input);
    }
  }

  if (status.isPending) return <LoadingRegion label="Carregando assistente…"><Skeleton className="mx-auto h-64 max-w-3xl rounded-xl" /></LoadingRegion>;
  if (status.isError) return <ErrorState title="Não foi possível falar com o assistente" error={status.error} onRetry={() => void status.refetch()} />;
  if (!ready) return <NotConfigured disabled={status.data.configured && !status.data.enabled} />;

  const history = (
    <History list={conversations.data} current={conversationId} onOpen={openConversation} onNew={newConversation}
      onDelete={(c) => { if (window.confirm(`Excluir a conversa "${c.title}"?`)) del.mutate(c.id); }} />
  );
  const empty = messages.length === 0 && !(conversationId && conv.isPending);

  return (
    <div className="mx-auto flex h-[calc(100dvh-6rem)] max-w-6xl gap-4 sm:h-[calc(100dvh-7rem)]">
      <aside className="hidden w-64 shrink-0 lg:block">{history}</aside>
      <section className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-xl border border-line bg-surface shadow-card" aria-label="Conversa com o assistente">
        <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-2.5">
          <div className="flex min-w-0 items-center gap-2">
            <Button size="sm" variant="ghost" className="lg:hidden" onClick={() => setHistoryOpen(true)}>Conversas</Button>
            <p className="truncate text-caption text-fg-subtle">Responde só com os dados da sua empresa, com o seu perfil de acesso.</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {status.data.model && <Badge tone="neutral" title={`Provedor: ${status.data.provider ?? "—"}`}>{status.data.model}</Badge>}
            <Button size="sm" variant="ghost" className="hidden sm:inline-flex lg:hidden" onClick={newConversation}>Nova</Button>
          </div>
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto px-4 py-5" data-testid="chat-log">
          {conversationId && conv.isError && <ErrorState title="Não foi possível abrir a conversa" error={conv.error} onRetry={() => void conv.refetch()} />}
          {conversationId && conv.isPending && messages.length === 0 && <LoadingRegion label="Abrindo conversa…"><Skeleton className="h-24" /></LoadingRegion>}
          {empty && (
            <div className="mx-auto max-w-xl space-y-4 py-6 text-center">
              <p className="text-title text-fg">O que você quer saber da fábrica?</p>
              <p className="text-body text-fg-muted">Cada número da resposta vem de uma consulta aos seus dados e tem um link para a tela que o mostra. Se faltar dado, o assistente diz — não adivinha.</p>
              <ul className="grid gap-2 text-left sm:grid-cols-2" data-testid="suggestions">
                {suggestions.map((s) => (
                  <li key={s}>
                    <button type="button" onClick={() => void send(s)} className="min-h-12 w-full rounded-xl border border-line bg-surface px-3 py-2 text-left text-body text-fg transition-colors hover:border-accent hover:bg-accent/5">{s}</button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {messages.map((m) => m.role === "user" ? (
            <div key={m.key} className="flex justify-end">
              <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-accent/15 px-4 py-2.5 text-body text-fg" data-testid="user-message">{m.text}</p>
            </div>
          ) : (
            <article key={m.key} className="flex gap-3" data-testid="assistant-message" aria-busy={m.status === "streaming" || undefined}>
              <span aria-hidden="true" className="flex size-8 shrink-0 items-center justify-center rounded-full bg-accent/15 text-caption font-bold text-ok">IA</span>
              <div className="min-w-0 flex-1 space-y-3">
                <ToolSteps steps={m.steps} live={m.status === "streaming"} verifying={m.verifying} />
                {m.text && <Markdown text={m.text} citations={m.response?.citations ?? []} />}
                {m.status === "streaming" && m.text && <span aria-hidden="true" className="inline-block h-4 w-1.5 animate-pulse bg-fg-subtle align-middle" />}
                {m.status === "error" && <p role="alert" className="rounded-lg bg-danger-solid/10 px-3 py-2 text-body text-danger">{m.error}</p>}
                {m.status === "done" && m.response && <AnswerFooter r={m.response} />}
              </div>
            </article>
          ))}
          <div ref={bottomRef} />
        </div>

        <form className="border-t border-line p-3" onSubmit={(e) => { e.preventDefault(); void send(input); }}>
          <div className="flex items-end gap-2">
            <label htmlFor="ai-input" className="sr-only">Pergunta</label>
            <textarea id="ai-input" ref={inputRef} rows={1} value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={onKey}
              placeholder="Pergunte sobre OEE, paradas, manutenção, componentes…" maxLength={4000}
              className="max-h-40 min-h-12 flex-1 resize-none rounded-xl border border-line-strong bg-surface px-4 py-3 text-body text-fg placeholder:text-fg-subtle focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30" />
            {busy
              ? <Button size="lg" onClick={() => abortRef.current?.abort()}>Parar</Button>
              : <Button size="lg" type="submit" variant="primary" disabled={!input.trim()}>Enviar</Button>}
          </div>
          <p className="mt-1.5 hidden text-caption text-fg-subtle sm:block">Enter envia · Shift+Enter quebra linha · o assistente só lê dados (não abre OS nem muda nada).</p>
        </form>
      </section>
      <Sheet open={historyOpen} onClose={() => setHistoryOpen(false)} title="Conversas">{history}</Sheet>
    </div>
  );
}
