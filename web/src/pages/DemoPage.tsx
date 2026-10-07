import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { useOpenDemo } from "@/hooks/useOpenDemo";

/**
 * /demo — target of the public site's "Ver demonstração" ({APP_URL}/demo). Opens the demo session on load and
 * goes to /dashboard; on 429/503 it says why and offers to try again. Public route (outside ProtectedRoute).
 */
export default function DemoPage() {
  const { open, busy, error } = useOpenDemo();
  const started = useRef(false);
  useEffect(() => {
    // StrictMode mounts twice in dev: open only one session per visit.
    if (started.current) return;
    started.current = true;
    void open();
  }, [open]);

  return (
    <div data-theme="dark" className="flex min-h-screen items-center justify-center bg-gray-950 px-4 font-sans text-gray-100">
      <div className="w-full max-w-sm text-center">
        {error ? (
          <>
            <p role="alert" className="text-sm text-red-300">{error}</p>
            <div className="mt-5 flex flex-col items-center gap-3">
              <button type="button" onClick={() => void open()} disabled={busy} data-testid="retry-demo"
                className="rounded-xl border border-brand-500/60 bg-brand-500/10 px-6 py-3 text-sm font-semibold text-brand-300 hover:bg-brand-500/20 disabled:opacity-60">
                {busy ? "Abrindo demonstração…" : "Tentar de novo"}
              </button>
              <Link to="/" className="text-sm text-gray-400 hover:text-gray-200">Voltar ao início</Link>
            </div>
          </>
        ) : (
          <div role="status" aria-busy="true" className="flex flex-col items-center gap-3">
            <span className="size-6 animate-spin rounded-full border-2 border-brand-400 border-t-transparent" />
            <p className="text-sm text-gray-300">Abrindo demonstração…</p>
            <p className="text-xs text-fg-muted">Dados de uma planta virtual, somente leitura.</p>
          </div>
        )}
      </div>
    </div>
  );
}
