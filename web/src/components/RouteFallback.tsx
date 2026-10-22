/** Spinner shown while a lazily loaded screen downloads (route-level code splitting). */
export default function RouteFallback() {
  return (
    <div role="status" aria-busy="true" className="flex min-h-[50vh] items-center justify-center">
      <span className="sr-only">Carregando…</span>
      <span aria-hidden="true" className="size-6 animate-spin rounded-full border-2 border-accent border-t-transparent" />
    </div>
  );
}
