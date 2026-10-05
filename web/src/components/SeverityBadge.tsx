import clsx from "clsx";
import type { Severity } from "@/services/types";

const C: Record<Severity, { label: string; cls: string }> = {
  info:     { label: "Info",     cls: "bg-blue-500/10 text-blue-400" },
  warning:  { label: "Alerta",   cls: "bg-yellow-500/10 text-yellow-400" },
  critical: { label: "Crítico",  cls: "bg-red-500/10 text-red-400" },
};

export default function SeverityBadge({ severity }: { severity: Severity }) {
  // unexpected values degrade to "info" instead of crashing
  const known = severity in C;
  const { label, cls } = known ? C[severity] : C.info;
  return (
    <span
      data-severity={known ? severity : "info"}
      className={clsx("whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium", cls)}
    >
      {label}
    </span>
  );
}
