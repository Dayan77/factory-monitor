import { useLocation } from "react-router-dom";
import ConnectionBadge from "./ConnectionBadge";
import ThemeToggle from "./ThemeToggle";
import NotificationBell from "./notifications/NotificationCenter";

const TITLES: Record<string, string> = {
  "/dashboard": "Raio-X",
  "/oee": "Análise de OEE",
  "/timeline": "Linha do tempo",
  "/stops": "Paradas",
  "/reports": "Relatórios",
  "/settings": "Configurações",
  "/settings/maintenance-plans": "Planos de manutenção",
  "/maintenance": "Manutenção",
  "/maintenance/upcoming": "Próximas manutenções",
  "/maintenance/components": "Componentes por % de vida",
  "/maintenance/work-orders": "Ordens de serviço",
  "/maintenance/schedule": "Programação da semana",
  "/maintenance/failures": "Falhas",
  "/maintenance/kpis": "Gestão da manutenção",
  "/maintenance/reliability": "Confiabilidade",
  "/maintenance/predictive": "Manutenção preditiva",
  "/settings/gateways": "Gateways de campo",
  "/factory": "Fábrica",
  "/lines": "Linhas de Produção",
  "/machines": "Máquinas",
  "/sensors": "Sensores",
  "/cameras": "Câmeras",
  "/alerts": "Alertas",
  "/ai": "Assistente de IA",
  "/admin": "Administração",
};

interface Props {
  onMenu?: () => void;
  menuOpen?: boolean;
}

/** Title for the current route (nested routes inherit the section title, e.g. /factory/machines/:id). */
export function titleFor(pathname: string): string {
  if (TITLES[pathname]) return TITLES[pathname];
  const section = "/" + (pathname.split("/")[1] ?? "");
  return TITLES[section] ?? "Factory Monitor";
}

export default function Header({ onMenu, menuOpen = false }: Props) {
  const { pathname } = useLocation();

  return (
    <header className="flex h-16 items-center justify-between gap-3 border-b border-line bg-surface/85 px-4 backdrop-blur supports-[backdrop-filter]:bg-surface/70 sm:px-6">
      <div className="flex min-w-0 items-center gap-2">
        {onMenu && (
          <button
            type="button"
            onClick={onMenu}
            aria-label="Abrir menu"
            aria-controls="app-sidebar"
            aria-expanded={menuOpen}
            className="-ml-2 flex size-11 items-center justify-center rounded-lg text-fg-muted hover:bg-surface-2 hover:text-fg md:hidden"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="size-5" aria-hidden="true">
              <path d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
        )}
        <h1 className="truncate text-heading text-fg">
          {titleFor(pathname)}
        </h1>
      </div>
      <div className="flex shrink-0 items-center gap-2 sm:gap-3">
        <ConnectionBadge />
        <span className="hidden text-caption text-fg-subtle lg:inline">
          {new Date().toLocaleDateString("pt-BR", {
            weekday: "short",
            day: "2-digit",
            month: "short",
            year: "numeric",
          })}
        </span>
        <NotificationBell />
        <ThemeToggle />
      </div>
    </header>
  );
}
