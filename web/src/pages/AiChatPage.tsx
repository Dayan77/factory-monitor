import { useState, useRef, useEffect } from "react";
import clsx from "clsx";

interface Message {
  id: number;
  role: "user" | "assistant";
  text: string;
}

const SUGGESTIONS = [
  "Por que a Linha 2 parou tanto ontem?",
  "Qual máquina teve pior OEE esta semana?",
  "Mostre o histórico de temperatura do Forno F-01.",
  "Quando foi a última manutenção do Compressor AR-02?",
];

const MOCK_REPLIES: Record<string, string> = {
  default:
    "Analisando os dados das últimas 24h… A Linha 2 apresentou 3 paradas não programadas totalizando 47 minutos de downtime, sendo a principal causa a falha no Forno Cura F-01 (temperatura fora do limite às 09h18). O OEE médio da linha foi de 61%, abaixo da meta de 85%.",
};

let msgId = 0;

export default function AiChatPage() {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: ++msgId,
      role: "assistant",
      text: "Olá! Sou o assistente de IA do Factory Monitor. Posso analisar dados de produção, identificar causas de parada, calcular OEE e gerar insights. O que você gostaria de saber?",
    },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  function send(text: string) {
    if (!text.trim() || loading) return;
    const userMsg: Message = { id: ++msgId, role: "user", text };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    // Simulate API latency
    setTimeout(() => {
      const reply: Message = {
        id: ++msgId,
        role: "assistant",
        text: MOCK_REPLIES.default,
      };
      setMessages((prev) => [...prev, reply]);
      setLoading(false);
    }, 1400);
  }

  return (
    <div className="flex h-full flex-col gap-4" style={{ maxHeight: "calc(100vh - 10rem)" }}>
      {/* Chat messages */}
      <div className="flex-1 overflow-y-auto rounded-xl border border-gray-800 bg-gray-900 p-5 space-y-4">
        {messages.map((m) => (
          <div
            key={m.id}
            className={clsx("flex gap-3", m.role === "user" && "flex-row-reverse")}
          >
            <span
              className={clsx(
                "shrink-0 size-8 rounded-full flex items-center justify-center text-xs font-bold",
                m.role === "assistant"
                  ? "bg-brand-500/20 text-brand-400"
                  : "bg-gray-700 text-gray-300"
              )}
            >
              {m.role === "assistant" ? "IA" : "DS"}
            </span>
            <div
              className={clsx(
                "max-w-[80%] rounded-xl px-4 py-3 text-sm leading-relaxed",
                m.role === "assistant"
                  ? "bg-gray-800 text-gray-200"
                  : "bg-brand-500/20 text-gray-100"
              )}
            >
              {m.text}
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex gap-3">
            <span className="shrink-0 size-8 rounded-full bg-brand-500/20 text-brand-400 flex items-center justify-center text-xs font-bold">
              IA
            </span>
            <div className="bg-gray-800 rounded-xl px-4 py-3 flex items-center gap-1.5">
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  className="size-1.5 rounded-full bg-gray-500 animate-bounce"
                  style={{ animationDelay: `${i * 0.15}s` }}
                />
              ))}
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Suggestions */}
      {messages.length < 3 && (
        <div className="flex flex-wrap gap-2">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              onClick={() => send(s)}
              className="rounded-full border border-gray-700 bg-gray-800 px-3 py-1.5 text-xs text-gray-300 hover:border-brand-500/50 hover:text-brand-300 transition-colors"
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {/* Input */}
      <form
        className="flex gap-3"
        onSubmit={(e) => { e.preventDefault(); send(input); }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Pergunte sobre produção, OEE, paradas, sensores…"
          className="flex-1 rounded-xl border border-gray-700 bg-gray-800 px-4 py-3 text-sm text-gray-100 placeholder-gray-600 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
        />
        <button
          type="submit"
          disabled={!input.trim() || loading}
          className="rounded-xl bg-brand-500 px-5 py-3 text-sm font-medium text-white transition-colors hover:bg-brand-600 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Enviar
        </button>
      </form>
    </div>
  );
}
