import type { ReactNode } from "react";
import clsx from "clsx";

interface Props {
  title: string;
  description?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
  className?: string;
}

export default function EmptyState({ title, description, icon, action, className }: Props) {
  return (
    <div className={clsx("flex flex-col items-center justify-center gap-2 px-5 py-10 text-center", className)}>
      {icon && <span className="mb-1 text-fg-subtle" aria-hidden="true">{icon}</span>}
      <p className="text-body font-medium text-fg">{title}</p>
      {description && <p className="max-w-md text-caption text-fg-subtle">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
