/** Role names in Portuguese (sidebar, account, invitations). */
export const ROLE_LABELS: Record<string, string> = {
  operator: "Operador",
  manager: "Gestor",
  admin: "Administrador",
  superadmin: "Super Admin",
  maintenance_planner: "Planejador de manutenção",
  technician: "Técnico de manutenção",
  production_supervisor: "Supervisor de produção",
  demo_viewer: "Visitante da demonstração",
};

/** Roles a tenant admin can grant (invitation / user forms). */
export const ASSIGNABLE_ROLES = [
  "operator", "production_supervisor", "maintenance_planner", "technician", "manager", "admin",
] as const;

export function roleList(u: { role?: string | null; roles?: string[] | null } | null | undefined): string {
  const r = u?.roles?.length ? u.roles : u?.role ? [u.role] : [];
  return r.map((x) => ROLE_LABELS[x] ?? x).join(" · ");
}
