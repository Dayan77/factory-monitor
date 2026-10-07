import { useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import clsx from "clsx";

export interface TabItem<V extends string> {
  value: V;
  label: ReactNode;
}

interface Props<V extends string> {
  items: readonly TabItem<V>[];
  value: V;
  onChange: (v: V) => void;
  /** Accessible name of the tab list. */
  label: string;
  /** id prefix: tabs get `${id}-tab-${value}` and control `${id}-panel`. */
  id: string;
  size?: "sm" | "md";
  className?: string;
}

/**
 * Segmented tabs (WAI-ARIA tabs pattern): roving tabindex, ←/→/Home/End move and activate.
 * Render the panel yourself with <TabPanel id={id} …>.
 */
export function Tabs<V extends string>({ items, value, onChange, label, id, size = "md", className }: Props<V>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  // aria-controls only when the panel exists: many tab rows are segmented filters with no <TabPanel>, and an
  // aria-controls pointing at a missing id is invalid ARIA (axe: aria-valid-attr-value)
  const [hasPanel, setHasPanel] = useState(false);
  useLayoutEffect(() => {
    const exists = !!document.getElementById(`${id}-panel`);
    if (exists !== hasPanel) setHasPanel(exists);
  });

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const i = items.findIndex((it) => it.value === value);
    let next = -1;
    if (e.key === "ArrowRight") next = (i + 1) % items.length;
    else if (e.key === "ArrowLeft") next = (i - 1 + items.length) % items.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = items.length - 1;
    if (next < 0) return;
    e.preventDefault();
    onChange(items[next].value);
    refs.current[next]?.focus();
  }

  return (
    <div
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
      className={clsx("inline-flex max-w-full gap-1 overflow-x-auto rounded-lg border border-line bg-surface-2/60 p-1", className)}
    >
      {items.map((it, idx) => {
        const selected = it.value === value;
        return (
          <button
            key={it.value}
            ref={(el) => { refs.current[idx] = el; }}
            id={`${id}-tab-${it.value}`}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={hasPanel ? `${id}-panel` : undefined}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(it.value)}
            className={clsx(
              "whitespace-nowrap rounded-md font-medium transition-colors duration-150",
              size === "sm" ? "min-h-8 px-2.5 text-caption" : "min-h-9 px-3 text-body",
              selected ? "bg-surface text-fg shadow-card" : "text-fg-muted hover:text-fg",
            )}
          >
            {it.label}
          </button>
        );
      })}
    </div>
  );
}

export function TabPanel({ id, activeValue, children, className }: { id: string; activeValue: string; children: ReactNode; className?: string }) {
  return (
    <div role="tabpanel" id={`${id}-panel`} aria-labelledby={`${id}-tab-${activeValue}`} className={className}>
      {children}
    </div>
  );
}
