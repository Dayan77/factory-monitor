import { Suspense, useCallback, useEffect, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { usePlant } from "@/hooks/useEfficiency";
import { normalizeCatalogParams } from "@/utils/deepLinks";
import { usePlantClock } from "@/hooks/usePlantClock";
import Sidebar from "./Sidebar";
import Header from "./Header";
import { useAuthStore } from "@/store/auth";
import { useRealtimeConnection } from "@/hooks/useWebSocket";
import EmergencyAlert from "./notifications/EmergencyAlert";
import CommandPalette from "./CommandPalette";
import RouteFallback from "./RouteFallback";
import DemoBanner, { DemoBlocked, useIsDemo } from "./DemoBanner";
import { isDemoBlockedPath } from "@/utils/demo";

/** AI citations use API-style params (start/stop, group_by, line_id…): translate them to each screen's URL state. */
function useCatalogDeepLinks() {
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  const { tree, tz } = usePlant();
  const needsTz = /[?&](start|stop)=/.test(search);
  useEffect(() => {
    if (!search || (needsTz && tree.isPending)) return;
    const next = normalizeCatalogParams(pathname, new URLSearchParams(search), tz);
    if (next) navigate({ pathname, search: next.toString() ? `?${next.toString()}` : "" }, { replace: true });
  }, [pathname, search, tz, needsTz, tree.isPending, navigate]);
}

/** App pages are never indexed (the public demo is reachable without sign-up — sprint6 §5 "noindex"). */
export function useNoIndex() {
  useEffect(() => {
    const m = document.createElement("meta");
    m.name = "robots";
    m.content = "noindex, nofollow";
    document.head.appendChild(m);
    return () => m.remove();
  }, []);
}

export default function Layout() {
  useNoIndex();
  const tenantId = useAuthStore((s) => s.user?.tenant_id);
  useRealtimeConnection(tenantId);
  useCatalogDeepLinks();
  usePlantClock();
  const [navOpen, setNavOpen] = useState(false);
  const closeNav = useCallback(() => setNavOpen(false), []);
  const demo = useIsDemo();
  const { pathname } = useLocation();

  return (
    <div className="flex h-screen overflow-hidden bg-canvas print:block print:h-auto print:overflow-visible">
      <a href="#conteudo" className="sr-only z-[70] rounded-lg bg-accent px-4 py-2 font-medium text-accent-fg focus:not-sr-only focus:fixed focus:left-3 focus:top-3">
        Pular para o conteúdo
      </a>
      <div className="contents print:hidden"><Sidebar open={navOpen} onClose={closeNav} /></div>
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden print:block print:overflow-visible">
        <DemoBanner />
        <div className="contents print:hidden"><Header onMenu={() => setNavOpen((v) => !v)} menuOpen={navOpen} /></div>
        <main id="conteudo" tabIndex={-1} className="flex-1 overflow-y-auto p-4 focus:outline-none sm:p-6 print:overflow-visible print:p-0">
          {/* lazy screens load inside the shell: menu and header stay in place */}
          <Suspense fallback={<RouteFallback />}>
            {demo && isDemoBlockedPath(pathname) ? <DemoBlocked /> : <Outlet />}
          </Suspense>
        </main>
      </div>
      <EmergencyAlert />
      <CommandPalette />
    </div>
  );
}
