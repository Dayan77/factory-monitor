import { useEffect } from "react";
import { NavLink, Outlet } from "react-router-dom";
import clsx from "clsx";
import ThemeToggle from "@/components/ThemeToggle";
import NotificationBell from "@/components/notifications/NotificationCenter";
import EmergencyAlert from "@/components/notifications/EmergencyAlert";
import { useRealtimeConnection } from "@/hooks/useWebSocket";
import { useOfflineSync, useQueueState } from "@/hooks/useTechnician";
import { useAuthStore } from "@/store/auth";
import { usePlantClock } from "@/hooks/usePlantClock";
import { registerServiceWorker } from "@/services/pwa";
import { clearFailed } from "@/services/offline";
import { can } from "@/utils/maintenance";
import { formatDateTime } from "@/utils/format";

function SyncPill() {
  const { online, pending, syncing } = useOfflineSync();
  const label = !online
    ? `Sem conexão${pending ? ` · ${pending} pendente${pending > 1 ? "s" : ""}` : ""}`
    : syncing ? "Sincronizando…" : pending ? `${pending} pendente${pending > 1 ? "s" : ""}` : "Online";
  return (
    <span role="status" data-testid="sync-status" data-online={online} data-pending={pending}
      className={clsx("inline-flex min-h-8 items-center gap-1.5 rounded-full px-3 text-caption font-semibold",
        !online ? "bg-warn-solid/20 text-warn" : pending ? "bg-info-solid/15 text-info" : "bg-ok-solid/15 text-ok")}>
      <span aria-hidden="true" className={clsx("size-2 rounded-full", !online ? "bg-warn-solid" : pending ? "bg-info-solid animate-pulse" : "bg-ok-solid")} />
      {label}
    </span>
  );
}

function FailedBanner() {
  const { failed } = useQueueState();
  if (failed.length === 0) return null;
  return (
    <div role="alert" className="mx-4 mt-3 rounded-xl border border-danger-solid/40 bg-danger-solid/10 p-3 text-caption text-fg">
      <p className="font-semibold text-danger">{failed.length} ação(ões) feitas sem conexão foram recusadas pelo servidor:</p>
      <ul className="mt-1 list-disc space-y-0.5 pl-5">
        {failed.slice(-5).map((f) => (
          <li key={f.action.id}>{f.action.wo_number ?? "OS"} · {f.action.kind === "task" ? "checklist" : f.action.kind === "labor" ? "apontamento" : "mudança de estado"} ({formatDateTime(f.action.created_at)}): {f.message}</li>
        ))}
      </ul>
      <button type="button" onClick={clearFailed} className="mt-2 min-h-10 font-semibold text-ok">Entendi</button>
    </div>
  );
}

const NAV = [
  { to: "/m/work-orders", label: "Minhas OS", icon: <path d="M9 5h10M9 12h10M9 19h10M4 5h.01M4 12h.01M4 19h.01" /> },
  { to: "/m/log", label: "Apontamentos", icon: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></> },
];

/** Technician mobile app shell (/m): installable PWA, offline queue status, bell, bottom nav (thumb zone). */
export default function MobileLayout() {
  const user = useAuthStore((s) => s.user);
  const offlineUser = useAuthStore((s) => s.offline);
  useRealtimeConnection(user?.tenant_id);
  usePlantClock();

  useEffect(() => {
    void registerServiceWorker();
    // warm the execution screen chunks (cached for offline use even where the service worker is unavailable) —
    // once the list is on screen and the phone is idle, never competing with the first paint
    const warm = () => { void import("./WorkOrderExecPage"); void import("./TodayLogPage"); };
    const w = window as Window & { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number; cancelIdleCallback?: (id: number) => void };
    if (w.requestIdleCallback) {
      const id = w.requestIdleCallback(warm, { timeout: 5000 });
      return () => w.cancelIdleCallback?.(id);
    }
    const t = window.setTimeout(warm, 2500);
    return () => window.clearTimeout(t);
  }, []);

  return (
    <div className="min-h-dvh bg-canvas text-fg">
      <header className="sticky top-0 z-30 border-b border-line bg-surface/95 backdrop-blur" style={{ paddingTop: "env(safe-area-inset-top)" }}>
        <div className="mx-auto flex h-14 max-w-xl items-center justify-between gap-2 px-3">
          <div className="flex min-w-0 items-center gap-2">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-brand-500" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="currentColor" className="size-5 text-white"><path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" /></svg>
            </span>
            <SyncPill />
          </div>
          <div className="flex shrink-0 items-center">
            <NotificationBell mobile />
            <ThemeToggle />
          </div>
        </div>
      </header>
      {offlineUser && (
        <p className="mx-4 mt-3 rounded-xl bg-warn-solid/15 px-3 py-2 text-caption text-warn">Sessão aberta sem conexão com o servidor (dados salvos no aparelho).</p>
      )}
      <FailedBanner />
      <main className="mx-auto max-w-xl px-4 pb-40 pt-4">
        <Outlet />
      </main>
      <nav aria-label="App do técnico" className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-surface/95 backdrop-blur" style={{ paddingBottom: "env(safe-area-inset-bottom)" }}>
        <ul className="mx-auto flex max-w-xl">
          {NAV.map((n) => (
            <li key={n.to} className="flex-1">
              <NavLink to={n.to} end={false}
                className={({ isActive }) => clsx("flex min-h-16 flex-col items-center justify-center gap-0.5 text-caption font-medium", isActive ? "text-ok" : "text-fg-muted")}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-6" aria-hidden="true">{n.icon}</svg>
                {n.label}
              </NavLink>
            </li>
          ))}
          {can(user, "maintenance.plan") && (
            <li className="flex-1">
              <NavLink to="/maintenance/work-orders" className="flex min-h-16 flex-col items-center justify-center gap-0.5 text-caption font-medium text-fg-muted">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-6" aria-hidden="true"><rect x="3" y="4" width="18" height="13" rx="2" /><path d="M8 21h8" /></svg>
                Painel
              </NavLink>
            </li>
          )}
        </ul>
      </nav>
      <EmergencyAlert mobile />
    </div>
  );
}
