import type { HTMLAttributes, ReactNode } from "react";
import clsx from "clsx";

type CardProps = HTMLAttributes<HTMLDivElement> & {
  /** Interactive cards get hover/focus affordances. */
  interactive?: boolean;
  padded?: boolean;
};

export function Card({ className, interactive, padded = true, ...rest }: CardProps) {
  return (
    <div
      className={clsx(
        "surface rounded-xl border border-line bg-surface shadow-card",
        padded && "p-4 sm:p-5",
        interactive && "transition-[border-color,box-shadow,transform] duration-200 ease-out-soft hover:-translate-y-px hover:border-line-strong hover:shadow-pop",
        className,
      )}
      {...rest}
    />
  );
}

interface CardHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  /** Heading level for the title (default h2). */
  as?: "h2" | "h3";
  id?: string;
  className?: string;
}

export function CardHeader({ title, description, action, as: H = "h2", id, className }: CardHeaderProps) {
  return (
    <div className={clsx("mb-4 flex flex-wrap items-start justify-between gap-3", className)}>
      <div className="min-w-0">
        <H id={id} className="text-heading text-fg">{title}</H>
        {description && <p className="mt-0.5 text-caption text-fg-subtle">{description}</p>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}
