import clsx from "clsx";
import type { MachineStatus } from "@/services/types";

const CONFIG: Record<MachineStatus, { label: string; dot: string; text: string; bg: string }> = {
  running:     { label: "Operando",       dot: "bg-brand-400 animate-pulse", text: "text-brand-400",  bg: "bg-brand-500/10" },
  stopped:     { label: "Parada",         dot: "bg-gray-400",                text: "text-gray-300",   bg: "bg-gray-700/30" },
  maintenance: { label: "Manutenção",     dot: "bg-yellow-400",              text: "text-yellow-400", bg: "bg-yellow-500/10" },
  error:       { label: "Falha",          dot: "bg-red-400 animate-pulse",   text: "text-red-400",    bg: "bg-red-500/10" },
  unknown:     { label: "Sem comunicação", dot: "bg-gray-500",               text: "text-gray-400",   bg: "bg-gray-800/60" },
};

/** Display order: most actionable first. */
export const STATUS_ORDER: readonly MachineStatus[] = ["error", "maintenance", "stopped", "unknown", "running"];

export function statusLabel(status: MachineStatus): string {
  return (CONFIG[status] ?? CONFIG.unknown).label;
}

interface Props {
  status: MachineStatus;
  size?: "sm" | "md";
}

export default function MachineStatusBadge({ status, size = "md" }: Props) {
  // unexpected values from the API degrade to "unknown" instead of crashing
  const c = CONFIG[status] ?? CONFIG.unknown;
  return (
    <span
      data-status={status in CONFIG ? status : "unknown"}
      className={clsx(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full font-medium",
        c.bg, c.text,
        size === "sm" ? "px-2 py-0.5 text-xs" : "px-2.5 py-1 text-xs"
      )}
    >
      <span aria-hidden="true" className={clsx("rounded-full", c.dot, size === "sm" ? "size-1.5" : "size-2")} />
      {c.label}
    </span>
  );
}
