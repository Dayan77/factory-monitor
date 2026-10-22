import type { ReactNode } from "react";
import clsx from "clsx";

export type Tone = "neutral" | "ok" | "warn" | "danger" | "info" | "accent";

const TONES: Record<Tone, { box: string; dot: string }> = {
  neutral: { box: "bg-surface-2 text-fg-muted", dot: "bg-neutral-solid" },
  ok:      { box: "bg-ok-solid/15 text-ok", dot: "bg-ok-solid" },
  warn:    { box: "bg-warn-solid/15 text-warn", dot: "bg-warn-solid" },
  danger:  { box: "bg-danger-solid/15 text-danger", dot: "bg-danger-solid" },
  info:    { box: "bg-info-solid/15 text-info", dot: "bg-info-solid" },
  accent:  { box: "bg-accent/15 text-ok", dot: "bg-accent" },
};

interface Props {
  tone?: Tone;
  dot?: boolean;
  /** Pulsing dot for live/critical states. */
  pulse?: boolean;
  children: ReactNode;
  className?: string;
  title?: string;
}

export default function Badge({ tone = "neutral", dot, pulse, children, className, title }: Props) {
  const t = TONES[tone];
  return (
    <span
      title={title}
      data-tone={tone}
      className={clsx(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-caption font-medium",
        t.box,
        className,
      )}
    >
      {dot && <span aria-hidden="true" className={clsx("size-1.5 rounded-full", t.dot, pulse && "animate-pulse")} />}
      {children}
    </span>
  );
}
