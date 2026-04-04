import { useRealtimeSensors } from "@/hooks/useWebSocket";
import clsx from "clsx";

export default function ConnectionBadge() {
  const { status } = useRealtimeSensors("demo-tenant");

  const label = {
    connected: "Ao vivo",
    connecting: "Conectando…",
    disconnected: "Desconectado",
  }[status];

  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium",
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
      {label}
    </span>
  );
}
