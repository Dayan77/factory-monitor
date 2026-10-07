import clsx from "clsx";

/** Placeholder block shown while data loads (keeps layout stable, no jumps). */
export function Skeleton({ className, style }: { className?: string; style?: React.CSSProperties }) {
  return <div aria-hidden="true" style={style} className={clsx("animate-pulse rounded-md bg-surface-2 [will-change:opacity]", className)} />;
}

/** Screen-reader friendly wrapper for a group of skeletons. */
export function LoadingRegion({ label = "Carregando…", children }: { label?: string; children: React.ReactNode }) {
  return (
    <div role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">{label}</span>
      {children}
    </div>
  );
}
