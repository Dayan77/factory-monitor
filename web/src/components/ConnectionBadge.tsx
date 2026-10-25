import clsx from "clsx";
import { useRealtimeStore } from "@/store/realtime";

const LABEL = {
  connected: "Ao vivo",
  connecting: "Conectando…",
  disconnected: "Desconectado",
} as const;

export default function ConnectionBadge() {
  const status = useRealtimeStore((s) => s.status);

  return (
    <span
      role="status"
      aria-label={`Tempo real: ${LABEL[status]}`}
      className={clsx(
        // fixed width: "Conectando…" → "Ao vivo" must not shift the header (CLS)
        "inline-flex min-w-[6.25rem] items-center justify-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-medium",
        status === "connected"     && "bg-brand-500/15 text-brand-400",
        status === "connecting"    && "bg-yellow-500/15 text-yellow-400",
        status === "disconnected"  && "bg-red-500/15 text-red-400"
      )}
    >
      <span
        className={clsx(
          "size-1.5 rounded-full",
          status === "connected"    && "bg-brand-400 animate-pulse",
          status === "connecting"   && "bg-yellow-400 animate-pulse",
          status === "disconnected" && "bg-red-400"
        )}
      />
      {LABEL[status]}
    </span>
  );
}
