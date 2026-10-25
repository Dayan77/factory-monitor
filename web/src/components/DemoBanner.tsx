import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useAuthStore } from "@/store/auth";
import { demoApi } from "@/services/sprint6Api";
import { Card } from "@/components/ui/Card";
import EmptyState from "@/components/ui/EmptyState";
import { DEMO_AREA_MSG, DEMO_BANNER, isDemoUser } from "@/utils/demo";

export function useIsDemo(): boolean {
  return useAuthStore((s) => isDemoUser(s.user));
}

/** Demo plant status: "planta simulada temporariamente parada" when the virtual plant sent nothing for 5 min. */
export function useDemoStatus(enabled: boolean) {
  return useQuery({
    queryKey: ["demo", "status"],
    queryFn: () => demoApi.status().then((r) => r.data),
    enabled,
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
}

/**
 * Permanent banner of the public demo on every screen (sprint6 §5): the data are simulated and that is always said
 * (project rule: no fake data without a warning).
 */
export default function DemoBanner({ compact = false }: { compact?: boolean }) {
  const demo = useIsDemo();
  const status = useDemoStatus(demo);
  if (!demo) return null;
  const stopped = status.data?.plant_running === false;
  const text = status.data?.banner ?? DEMO_BANNER;
  return (
    <div
      role="status"
      data-testid="demo-banner"
      className={
        "flex items-center justify-center gap-2 border-b px-4 text-center font-medium print:hidden " +
        (stopped ? "border-[#b45309] bg-[#fef3c7] text-[#78350f]" : "border-[#1e3a8a] bg-[#1d4ed8] text-white") +
        (compact ? " py-1 text-caption" : " py-1.5 text-caption sm:text-body")
      }
    >
      <svg viewBox="0 0 20 20" fill="currentColor" className="size-4 shrink-0" aria-hidden="true">
        <path fillRule="evenodd" d="M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Zm-7-4a1 1 0 1 1-2 0 1 1 0 0 1 2 0ZM9 9a.75.75 0 0 0 0 1.5h.25v3.25a.75.75 0 0 0 1.5 0V9.75A.75.75 0 0 0 10 9H9Z" clipRule="evenodd" />
      </svg>
      <span>{text}</span>
      <span className="hidden opacity-90 md:inline">· somente leitura</span>
    </div>
  );
}

/** Route guard: administration, users, gateways, settings and cameras are not part of the demo. */
export function DemoBlocked() {
  return (
    <div className="mx-auto max-w-3xl">
      <Card>
        <EmptyState title="Indisponível na demonstração" description={DEMO_AREA_MSG}
          action={<Link to="/dashboard" className="text-body font-medium text-ok hover:underline">Voltar ao Raio-X</Link>} />
      </Card>
    </div>
  );
}
