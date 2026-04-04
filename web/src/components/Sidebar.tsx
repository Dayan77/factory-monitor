import { NavLink } from "react-router-dom";
import clsx from "clsx";

const NAV = [
  {
    label: "Dashboard",
    to: "/dashboard",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5">
        <rect x="3" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="3" y="14" width="7" height="7" rx="1" />
        <rect x="14" y="14" width="7" height="7" rx="1" />
      </svg>
    ),
  },
  {
    label: "Linhas",
    to: "/lines",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5">
        <path d="M3 6h18M3 12h18M3 18h18" />
      </svg>
    ),
  },
  {
    label: "Máquinas",
    to: "/machines",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5">
        <rect x="4" y="4" width="16" height="16" rx="2" />
        <path d="M9 9h6v6H9z" />
        <path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3" />
      </svg>
    ),
  },
  {
    label: "Alertas",
    to: "/alerts",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5">
        <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
        <line x1="12" y1="9" x2="12" y2="13" />
        <line x1="12" y1="17" x2="12.01" y2="17" />
      </svg>
    ),
  },
  {
    label: "IA & Chat",
    to: "/ai",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        <path d="M8 10h8M8 14h5" />
      </svg>
    ),
  },
];

export default function Sidebar() {
  return (
    <aside className="flex w-60 flex-col border-r border-gray-800 bg-gray-900">
      {/* Logo */}
      <div className="flex h-16 items-center gap-3 border-b border-gray-800 px-5">
        <span className="flex size-8 items-center justify-center rounded-lg bg-brand-500">
          <svg viewBox="0 0 24 24" fill="currentColor" className="size-5 text-white">
            <path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" />
          </svg>
        </span>
        <div>
          <p className="text-sm font-semibold text-white">Factory Monitor</p>
          <p className="text-xs text-gray-500">Indústria 4.0</p>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 space-y-0.5 p-3">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              clsx(
                "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors",
                isActive
                  ? "bg-brand-500/15 text-brand-400"
                  : "text-gray-400 hover:bg-gray-800 hover:text-gray-100"
              )
            }
          >
            {item.icon}
            {item.label}
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="border-t border-gray-800 p-4">
        <div className="flex items-center gap-3">
          <div className="size-8 rounded-full bg-gray-700 flex items-center justify-center text-xs font-bold text-gray-300">
            DS
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-gray-200">Dayan Santos</p>
            <p className="truncate text-xs text-gray-500">Administrador</p>
          </div>
        </div>
      </div>
    </aside>
  );
}
