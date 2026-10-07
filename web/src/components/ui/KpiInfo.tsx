import { useEffect, useId, useRef, useState } from "react";
import clsx from "clsx";
import { KPI_DOCS, kpiManualUrl, type KpiId } from "@/utils/kpiGlossary";

/**
 * "ⓘ" next to a KPI label: a small popover with the definition and a link to the manual section. Opens on click /
 * Enter / Space (works on touch screens, unlike a hover tooltip); Esc or a click outside closes it.
 */
export default function KpiInfo({ kpi, className }: { kpi: KpiId; className?: string }) {
  const [open, setOpen] = useState(false);
  const [alignRight, setAlignRight] = useState(false);
  const id = useId();
  const wrap = useRef<HTMLSpanElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const doc = KPI_DOCS[kpi];

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent | TouchEvent) {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") { e.stopPropagation(); setOpen(false); btn.current?.focus(); }
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("touchstart", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("touchstart", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span ref={wrap} className={clsx("relative inline-flex align-middle", className)}>
      <button
        ref={btn}
        type="button"
        aria-expanded={open}
        aria-controls={id}
        aria-label={`O que é ${doc.name}?`}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          // open towards the side with room (never past the viewport edge on a phone)
          const r = btn.current?.getBoundingClientRect();
          setAlignRight(!!r && r.left > window.innerWidth / 2);
          setOpen((v) => !v);
        }}
        className="-m-1.5 flex size-7 items-center justify-center rounded-full text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg"
        data-testid={`kpi-info-${kpi}`}
      >
        <svg viewBox="0 0 20 20" fill="currentColor" className="size-4" aria-hidden="true">
          <path fillRule="evenodd" d="M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0zm-7-4a1 1 0 1 1-2 0 1 1 0 0 1 2 0zM9 9a.75.75 0 0 0 0 1.5h.25v3.25a.75.75 0 0 0 1.5 0V9.75A.75.75 0 0 0 10 9H9z" clipRule="evenodd" />
        </svg>
      </button>
      {open && (
        <span
          id={id}
          role="note"
          className={clsx("absolute top-full z-30 mt-2 w-64 max-w-[calc(100vw-2rem)] animate-fade-in", alignRight ? "-right-2" : "-left-2",
            " rounded-lg border border-line bg-surface p-3 text-left text-caption font-normal normal-case tracking-normal text-fg shadow-pop")}
          onClick={(e) => e.stopPropagation()}
        >
          <span className="mb-1 block font-semibold">{doc.name}</span>
          <span className="block text-fg-muted">{doc.text}</span>
          <a href={kpiManualUrl(kpi)} target="_blank" rel="noreferrer" className="mt-2 inline-block font-medium text-ok underline-offset-2 hover:underline">
            Como é calculado (manual)<span className="sr-only"> — abre em nova aba</span>
          </a>
        </span>
      )}
    </span>
  );
}
