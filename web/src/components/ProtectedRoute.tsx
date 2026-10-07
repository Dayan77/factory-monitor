import { useEffect } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuthStore } from "@/store/auth";
import { isDemoUser } from "@/utils/demo";

/**
 * Gate for the authenticated app. The outlet is rendered only once the user is known: with a
 * stored token but no user yet we show the spinner and validate it (/auth/me) first. Rendering
 * the page before that and swapping it for the spinner when loadMe() starts would mount, unmount
 * and remount the whole route on every cold load (blank flash; TV mode lost its forced theme).
 * Once a user is loaded the outlet stays mounted, even if a later login() flips `loading`.
 */
export default function ProtectedRoute() {
  const user = useAuthStore((s) => s.user);
  const loading = useAuthStore((s) => s.loading);
  const loadMe = useAuthStore((s) => s.loadMe);
  const token = localStorage.getItem("access_token");
  const { pathname } = useLocation();

  useEffect(() => {
    if (!user && token && !loading) void loadMe();
  }, [user, token, loading, loadMe]);

  // public demo (sprint6 §5): the technician / operator apps are not part of it
  if (user && isDemoUser(user) && /^\/(m|op)(\/|$)/.test(pathname)) return <Navigate to="/dashboard" replace />;
  if (user) return <Outlet />;
  // the operator tablet has its own kiosk login (sprint6 §3)
  if (!token) return <Navigate to={pathname === "/op" || pathname.startsWith("/op/") ? "/op/login" : "/login"} replace />;

  return (
    <div role="status" aria-busy="true" className="flex h-screen items-center justify-center bg-canvas" data-testid="auth-pending">
      <span className="sr-only">Carregando…</span>
      <span className="size-6 animate-spin rounded-full border-2 border-accent border-t-transparent" />
    </div>
  );
}
