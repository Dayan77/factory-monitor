import type { ReactNode } from "react";
import clsx from "clsx";

/** Shared input styles (inputs, selects, textareas). */
export const inputClass =
  "w-full min-h-10 rounded-lg border border-line-strong bg-surface px-3 py-2 text-body text-fg placeholder:text-fg-subtle " +
  "transition-colors focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30 disabled:opacity-60";

interface Props {
  label: ReactNode;
  htmlFor: string;
  hint?: ReactNode;
  error?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function Field({ label, htmlFor, hint, error, children, className }: Props) {
  return (
    <div className={clsx("space-y-1.5", className)}>
      <label htmlFor={htmlFor} className="block text-caption font-medium text-fg-muted">{label}</label>
      {children}
      {error ? (
        <p className="text-caption text-danger" role="alert">{error}</p>
      ) : hint ? (
        <p className="text-caption text-fg-subtle">{hint}</p>
      ) : null}
    </div>
  );
}
