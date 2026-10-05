import clsx from "clsx";

/** Placeholder block shown while data loads (keeps layout stable, no jumps). */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={clsx("animate-pulse rounded-md bg-surface-2", className)} />;
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
