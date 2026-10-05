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
      {icon && <span className="mb-1 text-gray-600">{icon}</span>}
      <p className="text-sm font-medium text-gray-300">{title}</p>
      {description && <p className="max-w-md text-xs text-gray-500">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
