import { useLocation } from "react-router-dom";
import ConnectionBadge from "./ConnectionBadge";

const TITLES: Record<string, string> = {
  "/dashboard": "Dashboard",
  "/lines": "Linhas de Produção",
  "/machines": "Máquinas",
  "/alerts": "Alertas",
  "/ai": "IA & Chat",
};

export default function Header() {
  const { pathname } = useLocation();

  return (
    <header className="flex h-16 items-center justify-between border-b border-gray-800 bg-gray-900 px-6">
      <h1 className="text-base font-semibold text-gray-100">
        {TITLES[pathname] ?? "Factory Monitor"}
      </h1>
      <div className="flex items-center gap-4">
        <ConnectionBadge />
        <span className="text-xs text-gray-500">
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
