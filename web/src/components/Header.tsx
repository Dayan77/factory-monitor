import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { displayTimeZone } from "@/utils/format";
import ConnectionBadge from "./ConnectionBadge";
import { PaletteButton } from "./CommandPalette";
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
  "/factory": "Mapa da planta",
  "/factory/list": "Fábrica",
  "/factory/layout/edit": "Editor de layout",
  "/onboarding": "Implantação",
  "/reports/scheduled": "Relatórios agendados",
  "/lines": "Linhas de Produção",
  "/machines": "Máquinas",
  "/sensors": "Sensores",
  "/cameras": "Câmeras",
  "/alerts": "Alertas",
  "/ai": "Assistente de IA",
  "/admin": "Administração",
  "/account": "Minha conta",
  "/settings/users": "Usuários e convites",
};

interface Props {
  onMenu?: () => void;
  menuOpen?: boolean;
}

/** Title for the current route (nested routes inherit the section title, e.g. /factory/machines/:id). */
export function titleFor(pathname: string): string {
  if (TITLES[pathname]) return TITLES[pathname];
  if (pathname.startsWith("/factory/")) return "Fábrica";
  if (pathname.startsWith("/onboarding/")) return "Implantação";
  const section = "/" + (pathname.split("/")[1] ?? "");
  return TITLES[section] ?? "FactoryConnect";
}

export default function Header({ onMenu, menuOpen = false }: Props) {
  const { pathname } = useLocation();
  const title = titleFor(pathname);
  // every screen has its own document title (WCAG 2.4.2; browser history, tabs, screen readers)
  useEffect(() => {
    document.title = title === "FactoryConnect" ? title : `${title} · FactoryConnect`;
  }, [title]);

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
          {title}
        </h1>
      </div>
      <div className="flex shrink-0 items-center gap-2 sm:gap-3">
        <PaletteButton />
        <ConnectionBadge />
        <span className="hidden text-caption text-fg-subtle lg:inline">
          {new Date().toLocaleDateString("pt-BR", {
            timeZone: displayTimeZone(),
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
