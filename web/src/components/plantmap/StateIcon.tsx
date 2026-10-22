import clsx from "clsx";
import type { VisualState } from "@/services/sprint6Types";
import { STATE_META } from "@/utils/visualState";

const PATHS: Record<string, JSX.Element> = {
  "cloud-off": (
    <>
      <path d="M3 3l18 18" />
      <path d="M8.5 7.6A5 5 0 0 1 16.9 10 4 4 0 0 1 20 17M17 18H7a4.5 4.5 0 0 1-1.2-8.84" />
    </>
  ),
  wrench: <path d="M14.7 6.3a4 4 0 0 0-5.4 5.2L3 17.8V21h3.2l6.3-6.3a4 4 0 0 0 5.2-5.4l-2.6 2.6-2.4-.6-.6-2.4z" />,
  "hard-hat": (
    <>
      <path d="M2 18h20M4 18v-2a8 8 0 0 1 16 0v2" />
      <path d="M10 8V5h4v3" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  moon: <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />,
  pause: <path d="M9 6v12M15 6v12" />,
  play: <path d="M8 5v14l11-7z" fill="currentColor" />,
};

/** Icon of a visual state (never colour only, sprint6 §1.2). */
export default function StateIcon({ state, className }: { state: VisualState; className?: string }) {
  const name = STATE_META[state].icon;
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round"
      className={clsx("shrink-0", className ?? "size-4")} aria-hidden="true" data-icon={name}>
      {PATHS[name]}
    </svg>
  );
}

/** Chip with colour + icon + text of a state (legend, side panel, operator tablet). */
export function StateChip({ state, text, className }: { state: VisualState; text?: string; className?: string }) {
  const meta = STATE_META[state];
  return (
    <span data-state={state} className={clsx("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-caption font-semibold", meta.marker, className)}>
      <StateIcon state={state} className="size-3.5" />
      {text ?? meta.label}
    </span>
  );
}
