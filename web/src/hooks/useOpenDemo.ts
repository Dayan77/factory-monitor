import { useCallback, useState } from "react";
import { isAxiosError } from "axios";
import { useNavigate } from "react-router-dom";
import { demoApi } from "@/services/sprint6Api";
import { useAuthStore } from "@/store/auth";
import { apiErrorMessage } from "@/utils/errors";

export const DEMO_MSG_RATE_LIMIT = "Muitas demonstrações abertas deste endereço. Tente novamente mais tarde.";
export const DEMO_MSG_UNAVAILABLE = "Demonstração indisponível no momento. Tente de novo em alguns minutos.";

/**
 * Opens a 2 h read-only session in the public demo tenant (POST /api/public/demo-session), keeps the JWT only
 * in this origin's storage and goes to the dashboard. Shared by the landing button and the /demo route that the
 * public site links to (site/src/lib/demo.ts).
 */
export function useOpenDemo() {
  const navigate = useNavigate();
  const loadMe = useAuthStore((st) => st.loadMe);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const open = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await demoApi.createSession();
      localStorage.setItem("access_token", r.data.access_token);
      localStorage.removeItem("refresh_token");
      await loadMe();
      navigate(r.data.redirect_to ?? "/dashboard", { replace: true });
    } catch (err) {
      const st = isAxiosError(err) ? err.response?.status : undefined;
      setError(st === 429 ? DEMO_MSG_RATE_LIMIT
        : st === 503 || st === 404 ? DEMO_MSG_UNAVAILABLE
        : apiErrorMessage(err, DEMO_MSG_UNAVAILABLE));
    } finally {
      setBusy(false);
    }
  }, [loadMe, navigate]);

  return { open, busy, error };
}
