import { useEffect, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { usePlant } from "@/hooks/useEfficiency";
import { normalizeCatalogParams } from "@/utils/deepLinks";
import Sidebar from "./Sidebar";
import Header from "./Header";
import { useAuthStore } from "@/store/auth";
import { useRealtimeConnection } from "@/hooks/useWebSocket";
import EmergencyAlert from "./notifications/EmergencyAlert";

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

export default function Layout() {
  const tenantId = useAuthStore((s) => s.user?.tenant_id);
  useRealtimeConnection(tenantId);
  useCatalogDeepLinks();
  const [navOpen, setNavOpen] = useState(false);

  return (
    <div className="flex h-screen overflow-hidden bg-canvas print:block print:h-auto print:overflow-visible">
      <div className="contents print:hidden"><Sidebar open={navOpen} onClose={() => setNavOpen(false)} /></div>
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden print:block print:overflow-visible">
        <div className="contents print:hidden"><Header onMenu={() => setNavOpen((v) => !v)} menuOpen={navOpen} /></div>
        <main className="flex-1 overflow-y-auto p-4 sm:p-6 print:overflow-visible print:p-0">
          <Outlet />
        </main>
      </div>
      <EmergencyAlert />
    </div>
  );
}
