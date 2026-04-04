import clsx from "clsx";

type Severity = "info" | "warning" | "critical";

const C: Record<Severity, { label: string; cls: string }> = {
  info:     { label: "Info",     cls: "bg-blue-500/10 text-blue-400" },
  warning:  { label: "Alerta",   cls: "bg-yellow-500/10 text-yellow-400" },
  critical: { label: "Crítico",  cls: "bg-red-500/10 text-red-400" },
};

export default function SeverityBadge({ severity }: { severity: Severity }) {
  const { label, cls } = C[severity];
  return (
    <span className={clsx("rounded-full px-2 py-0.5 text-xs font-medium", cls)}>
      {label}
    </span>
  );
}
