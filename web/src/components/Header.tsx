import { useLocation } from "react-router-dom";
import ConnectionBadge from "./ConnectionBadge";

const TITLES: Record<string, string> = {
  "/dashboard": "Dashboard",
  "/lines": "Linhas de Produção",
  "/machines": "Máquinas",
  "/sensors": "Sensores",
  "/cameras": "Câmeras",
  "/alerts": "Alertas",
  "/ai": "IA & Chat",
  "/admin": "Administração",
};

interface Props {
  onMenu?: () => void;
  menuOpen?: boolean;
}

export default function Header({ onMenu, menuOpen = false }: Props) {
  const { pathname } = useLocation();

  return (
    <header className="flex h-16 items-center justify-between gap-3 border-b border-gray-800 bg-gray-900 px-4 sm:px-6">
      <div className="flex min-w-0 items-center gap-2">
        {onMenu && (
          <button
            type="button"
            onClick={onMenu}
            aria-label="Abrir menu"
            aria-controls="app-sidebar"
            aria-expanded={menuOpen}
            className="-ml-2 flex size-11 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-800 hover:text-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 md:hidden"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="size-5" aria-hidden="true">
              <path d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
        )}
        <h1 className="truncate text-base font-semibold text-gray-100">
          {TITLES[pathname] ?? "Factory Monitor"}
        </h1>
      </div>
      <div className="flex shrink-0 items-center gap-4">
        <ConnectionBadge />
        <span className="hidden text-xs text-gray-500 sm:inline">
          {new Date().toLocaleDateString("pt-BR", {
            weekday: "short",
            day: "2-digit",
            month: "short",
            year: "numeric",
          })}
        </span>
      </div>
    </header>
  );
}
