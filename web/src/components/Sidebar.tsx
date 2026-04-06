import { NavLink, useNavigate } from "react-router-dom";
import clsx from "clsx";
import { useAuthStore } from "@/store/auth";

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
    label: "Sensores",
    to: "/sensors",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5">
        <path d="M22 12h-4l-3 9L9 3l-3 9H2"/>
      </svg>
    ),
  },
  {
    label: "Câmeras",
    to: "/cameras",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5">
        <path d="M15 10l4.553-2.069A1 1 0 0 1 21 8.87v6.26a1 1 0 0 1-1.447.899L15 14" />
        <rect x="2" y="6" width="13" height="12" rx="2" />
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

const ADMIN_NAV = {
  label: "Admin",
  to: "/admin",
  icon: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5">
      <circle cx="12" cy="8" r="4" />
      <path d="M20 21a8 8 0 1 0-16 0" />
      <path d="M16 19l2 2 4-4" />
    </svg>
  ),
};

const ROLE_LABELS: Record<string, string> = {
  operator:   "Operador",
  manager:    "Gestor",
  admin:      "Administrador",
  superadmin: "Super Admin",
};

export default function Sidebar() {
  const { user, logout } = useAuthStore();
  const navigate = useNavigate();

  function handleLogout() {
    logout();
    navigate("/login", { replace: true });
  }

  const initials = user?.name
    .split(" ")
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase() ?? "??";

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

        {user?.role === "superadmin" && (
          <>
            <div className="my-2 border-t border-gray-800" />
            <NavLink
              to={ADMIN_NAV.to}
              className={({ isActive }) =>
                clsx(
                  "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-purple-500/15 text-purple-400"
                    : "text-gray-500 hover:bg-gray-800 hover:text-gray-100"
                )
              }
            >
              {ADMIN_NAV.icon}
              {ADMIN_NAV.label}
            </NavLink>
          </>
        )}
      </nav>

      {/* Footer */}
      <div className="border-t border-gray-800 p-4 space-y-3">
        <div className="flex items-center gap-3">
          <div className="size-8 rounded-full bg-gray-700 flex items-center justify-center text-xs font-bold text-gray-300 shrink-0">
            {initials}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-gray-200">{user?.name ?? "—"}</p>
            <p className="truncate text-xs text-gray-500">{ROLE_LABELS[user?.role ?? ""] ?? user?.role}</p>
          </div>
        </div>
        <button
          onClick={handleLogout}
          className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-xs text-gray-500 hover:bg-gray-800 hover:text-gray-300 transition-colors"
        >
          <svg viewBox="0 0 20 20" fill="currentColor" className="size-4">
            <path fillRule="evenodd" d="M3 4.25A2.25 2.25 0 0 1 5.25 2h5.5A2.25 2.25 0 0 1 13 4.25v2a.75.75 0 0 1-1.5 0v-2a.75.75 0 0 0-.75-.75h-5.5a.75.75 0 0 0-.75.75v11.5c0 .414.336.75.75.75h5.5a.75.75 0 0 0 .75-.75v-2a.75.75 0 0 1 1.5 0v2A2.25 2.25 0 0 1 10.75 18h-5.5A2.25 2.25 0 0 1 3 15.75V4.25z" clipRule="evenodd" />
            <path fillRule="evenodd" d="M19 10a.75.75 0 0 0-.75-.75H8.704l1.048-1.096a.75.75 0 1 0-1.004-1.115l-2.5 2.25a.75.75 0 0 0 0 1.115l2.5 2.25a.75.75 0 1 0 1.004-1.115l-1.048-1.097h9.546A.75.75 0 0 0 19 10z" clipRule="evenodd" />
          </svg>
          Sair
        </button>
      </div>
    </aside>
  );
}
