import { NavLink, useNavigate } from "react-router-dom";
import clsx from "clsx";
import { useAuthStore } from "@/store/auth";
import { can, type Capability } from "@/utils/maintenance";
import { isDemoBlockedPath, isDemoUser } from "@/utils/demo";

const ICON_RAIOX = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden="true">
        <circle cx="12" cy="12" r="9" /><path d="M12 12l4-4" /><path d="M12 3v2M21 12h-2M12 21v-2M3 12h2" />
      </svg>
    );
const ICON_OEE = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden="true">
        <path d="M3 3v18h18" /><path d="m7 15 4-4 3 3 5-6" />
      </svg>
    );
const ICON_TIMELINE = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden="true">
        <path d="M3 6h7M13 6h8M3 12h4M10 12h11M3 18h10M16 18h5" />
      </svg>
    );
const ICON_STOPS = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden="true">
        <rect x="4" y="4" width="16" height="16" rx="3" /><path d="M10 9v6M14 9v6" />
      </svg>
    );
const ICON_REPORT = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden="true">
        <path d="M6 2h9l5 5v15H6z" /><path d="M14 2v6h6M9 13h8M9 17h5" />
      </svg>
    );
const ICON_SETTINGS = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden="true">
        <circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
      </svg>
    );
const ICON_FACTORY = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5">
        <path d="M2 20h20M4 20V9l5 3V9l5 3V5h4l1 15" />
        <path d="M8 16h1M12 16h1M16 16h1" />
      </svg>
    );
const ICON_LINES = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5">
        <path d="M3 6h18M3 12h18M3 18h18" />
      </svg>
    );
const ICON_MACHINES = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5">
        <rect x="4" y="4" width="16" height="16" rx="2" />
        <path d="M9 9h6v6H9z" />
        <path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3" />
      </svg>
    );
const ICON_SENSORS = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5">
        <path d="M22 12h-4l-3 9L9 3l-3 9H2"/>
      </svg>
    );
const ICON_CAMERAS = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5">
        <path d="M15 10l4.553-2.069A1 1 0 0 1 21 8.87v6.26a1 1 0 0 1-1.447.899L15 14" />
        <rect x="2" y="6" width="13" height="12" rx="2" />
      </svg>
    );
const ICON_ALERTS = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5">
        <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
        <line x1="12" y1="9" x2="12" y2="13" />
        <line x1="12" y1="17" x2="12.01" y2="17" />
      </svg>
    );
const ICON_AI = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        <path d="M8 10h8M8 14h5" />
      </svg>
    );

const ICON_WRENCH = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden="true">
        <path d="M14.7 6.3a4 4 0 0 0-5.4 5.2L3 17.8V21h3.2l6.3-6.3a4 4 0 0 0 5.2-5.4l-2.6 2.6-2.4-.6-.6-2.4z" />
      </svg>
    );
const ICON_CALENDAR = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden="true">
        <rect x="3" y="5" width="18" height="16" rx="2" /><path d="M3 10h18M8 3v4M16 3v4" />
      </svg>
    );
const ICON_BOARD = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden="true">
        <rect x="3" y="4" width="5" height="16" rx="1.5" /><rect x="10" y="4" width="5" height="10" rx="1.5" /><rect x="17" y="4" width="4" height="13" rx="1.5" />
      </svg>
    );
const ICON_LIFE = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden="true">
        <rect x="3" y="7" width="16" height="10" rx="2" /><path d="M21 10v4M6 10v4M9 10v4" />
      </svg>
    );
const ICON_FAILURE = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden="true">
        <circle cx="12" cy="12" r="9" /><path d="M12 7v6M12 16.5h.01" />
      </svg>
    );
const ICON_KPI = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden="true">
        <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
      </svg>
    );
const ICON_RELIABILITY = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden="true">
        <path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z" /><path d="m9 12 2 2 4-4" />
      </svg>
    );
const ICON_PHONE = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden="true">
        <rect x="6" y="2" width="12" height="20" rx="2.5" /><path d="M11 18h2" />
      </svg>
    );
const ICON_PLANS = ICON_CALENDAR;
const ICON_PREDICTIVE = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden="true">
        <path d="M3 17l5-5 4 3 4-6" /><path d="M16 9h4v4" strokeDasharray="2 2" /><path d="M3 21h18" />
      </svg>
    );
const ICON_GATEWAY = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden="true">
        <rect x="3" y="13" width="18" height="7" rx="2" /><path d="M7 16.5h.01M11 16.5h.01" /><path d="M8 9a6 6 0 0 1 8 0M5.5 6.5a10 10 0 0 1 13 0" /><path d="M12 13v-2" />
      </svg>
    );

const ICON_MAP = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden="true">
        <path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2z" /><path d="M9 4v14M15 6v14" />
      </svg>
    );
const ICON_TABLET = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden="true">
        <rect x="2" y="5" width="20" height="14" rx="2.5" /><path d="M18 12h.01" />
      </svg>
    );
const ICON_ROCKET = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden="true">
        <path d="M5 13l-2 6 6-2M14 4c3 0 6 3 6 6-2 4-6 7-9 8l-5-5c1-3 4-7 8-9z" /><circle cx="14.5" cy="9.5" r="1.5" />
      </svg>
    );
const ICON_MAIL = (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden="true">
        <rect x="3" y="5" width="18" height="14" rx="2" /><path d="m3 7 9 6 9-6" />
      </svg>
    );

interface NavItem { label: string; to: string; icon: JSX.Element; cap?: Capability; hide?: (u: { role?: string | null; roles?: string[] | null } | null) => boolean }

const NAV: { section: string; items: NavItem[] }[] = [
  {
    section: "Eficiência",
    items: [
      { label: "Raio-X", to: "/dashboard", icon: ICON_RAIOX },
      { label: "OEE", to: "/oee", icon: ICON_OEE },
      { label: "Linha do tempo", to: "/timeline", icon: ICON_TIMELINE },
      { label: "Paradas", to: "/stops", icon: ICON_STOPS },
      { label: "Relatório de turno", to: "/reports/shift", icon: ICON_REPORT },
      { label: "Relatórios agendados", to: "/reports/scheduled", icon: ICON_MAIL, cap: "reports.schedule" },
      { label: "Tablet do operador", to: "/op", icon: ICON_TABLET, cap: "operator.tablet" },
    ],
  },
  {
    section: "Manutenção",
    items: [
      { label: "Próximas manutenções", to: "/maintenance/upcoming", icon: ICON_WRENCH, cap: "maintenance.plan" },
      { label: "Ordens de serviço", to: "/maintenance/work-orders", icon: ICON_BOARD, cap: "maintenance.view" },
      { label: "Programação", to: "/maintenance/schedule", icon: ICON_CALENDAR, cap: "maintenance.plan" },
      { label: "Preditiva", to: "/maintenance/predictive", icon: ICON_PREDICTIVE, cap: "predictive.view" },
      { label: "Componentes", to: "/maintenance/components", icon: ICON_LIFE, cap: "maintenance.plan" },
      { label: "Falhas", to: "/maintenance/failures", icon: ICON_FAILURE, cap: "maintenance.failures" },
      { label: "Gestão", to: "/maintenance/kpis", icon: ICON_KPI, cap: "maintenance.kpis" },
      { label: "Confiabilidade", to: "/maintenance/reliability", icon: ICON_RELIABILITY, cap: "maintenance.kpis" },
      { label: "App do técnico", to: "/m/work-orders", icon: ICON_PHONE, cap: "maintenance.execute" },
    ],
  },
  {
    section: "Ativos",
    items: [
      { label: "Mapa da planta", to: "/factory", icon: ICON_MAP },
      { label: "Fábrica (lista)", to: "/factory/list", icon: ICON_FACTORY },
      { label: "Linhas", to: "/lines", icon: ICON_LINES },
      { label: "Máquinas", to: "/machines", icon: ICON_MACHINES },
    ],
  },
  {
    section: "Monitoramento",
    items: [
      { label: "Sensores", to: "/sensors", icon: ICON_SENSORS },
      { label: "Câmeras", to: "/cameras", icon: ICON_CAMERAS },
      { label: "Alertas", to: "/alerts", icon: ICON_ALERTS },
      { label: "Assistente IA", to: "/ai", icon: ICON_AI },
    ],
  },
  {
    section: "Sistema",
    items: [
      { label: "Implantação", to: "/onboarding", icon: ICON_ROCKET, cap: "onboarding.admin" },
      { label: "Configurações", to: "/settings/production", icon: ICON_SETTINGS },
      { label: "Planos de manutenção", to: "/settings/maintenance-plans", icon: ICON_PLANS, cap: "maintenance.plan" },
      { label: "Gateways", to: "/settings/gateways", icon: ICON_GATEWAY, cap: "gateways.view" },
    ],
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
  maintenance_planner: "Planejador de manutenção",
  technician: "Técnico de manutenção",
  production_supervisor: "Supervisor de produção",
  demo_viewer: "Visitante da demonstração",
};

interface SidebarProps {
  /** Mobile drawer state (ignored at md+ where the sidebar is always visible). */
  open?: boolean;
  onClose?: () => void;
}

export default function Sidebar({ open = false, onClose }: SidebarProps) {
  const { user, logout } = useAuthStore();
  const navigate = useNavigate();
  const demo = isDemoUser(user);

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
    <>
    {open && (
      <div
        aria-hidden="true"
        onClick={onClose}
        className="fixed inset-0 z-30 animate-fade-in bg-black/50 md:hidden"
      />
    )}
    <aside
      id="app-sidebar"
      className={clsx(
        "fixed inset-y-0 left-0 z-40 flex w-60 flex-col border-r border-line bg-surface transition-transform duration-300 ease-out-soft",
        "md:static md:translate-x-0",
        open ? "translate-x-0" : "-translate-x-full"
      )}
    >
      {/* Logo */}
      <div className="flex h-16 items-center gap-3 border-b border-line px-5">
        <span className="flex size-8 items-center justify-center rounded-lg bg-brand-500">
          <svg viewBox="0 0 24 24" fill="currentColor" className="size-5 text-white">
            <path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" />
          </svg>
        </span>
        <div>
          <p className="text-sm font-semibold text-fg">Factory Monitor</p>
          <p className="text-xs text-fg-subtle">Indústria 4.0</p>
        </div>
      </div>

      {/* Nav */}
      <nav aria-label="Principal" className="flex-1 space-y-0.5 overflow-y-auto p-3">
        {NAV.map((group) => ({
          ...group,
          // demo (sprint6 §5): no administration, users, gateways, settings or cameras
          items: group.items.filter((it) => (!it.cap || can(user, it.cap)) && (!it.hide || !it.hide(user)) && !(demo && isDemoBlockedPath(it.to))),
        }))
          .filter((group) => group.items.length > 0)
          .map((group) => (
          <div key={group.section} className="pb-2">
            <p className="px-3 pb-1 pt-2 text-overline uppercase text-fg-subtle">{group.section}</p>
            {group.items.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/factory"}
            onClick={onClose}
            className={({ isActive }) =>
              clsx(
                "flex min-h-10 items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors duration-150",
                isActive
                  ? "bg-accent/15 text-ok"
                  : "text-fg-muted hover:bg-surface-2 hover:text-fg"
              )
            }
          >
            {item.icon}
            {item.label}
          </NavLink>
            ))}
          </div>
        ))}

        {user?.role === "superadmin" && !demo && (
          <>
            <div className="my-2 border-t border-line" />
            <NavLink
              to={ADMIN_NAV.to}
              onClick={onClose}
              className={({ isActive }) =>
                clsx(
                  "flex min-h-11 items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors duration-150",
                  isActive
                    ? "bg-purple-500/15 text-purple-400"
                    : "text-fg-subtle hover:bg-surface-2 hover:text-fg"
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
      <div className="border-t border-line p-4 space-y-3">
        <div className="flex items-center gap-3">
          <div className="size-8 rounded-full bg-surface-2 flex items-center justify-center text-xs font-bold text-fg-muted shrink-0">
            {initials}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-fg">{user?.name ?? "—"}</p>
            <p className="truncate text-xs text-fg-subtle">
              {(user?.roles?.length ? user.roles : [user?.role ?? ""]).map((r) => ROLE_LABELS[r] ?? r).join(" · ")}
            </p>
          </div>
        </div>
        <button
          onClick={handleLogout}
          className="flex w-full items-center gap-2 rounded-lg px-3 py-2 min-h-10 text-xs text-fg-subtle hover:bg-surface-2 hover:text-fg transition-colors"
        >
          <svg viewBox="0 0 20 20" fill="currentColor" className="size-4">
            <path fillRule="evenodd" d="M3 4.25A2.25 2.25 0 0 1 5.25 2h5.5A2.25 2.25 0 0 1 13 4.25v2a.75.75 0 0 1-1.5 0v-2a.75.75 0 0 0-.75-.75h-5.5a.75.75 0 0 0-.75.75v11.5c0 .414.336.75.75.75h5.5a.75.75 0 0 0 .75-.75v-2a.75.75 0 0 1 1.5 0v2A2.25 2.25 0 0 1 10.75 18h-5.5A2.25 2.25 0 0 1 3 15.75V4.25z" clipRule="evenodd" />
            <path fillRule="evenodd" d="M19 10a.75.75 0 0 0-.75-.75H8.704l1.048-1.096a.75.75 0 1 0-1.004-1.115l-2.5 2.25a.75.75 0 0 0 0 1.115l2.5 2.25a.75.75 0 1 0 1.004-1.115l-1.048-1.097h9.546A.75.75 0 0 0 19 10z" clipRule="evenodd" />
          </svg>
          Sair
        </button>
      </div>
    </aside>
    </>
  );
}
