import ErrorState from "@/components/ui/ErrorState";
import { useState } from "react";
import { formatDate } from "@/utils/format";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { adminApi } from "@/services/api";
import type { TenantWithStats, TenantUser, TenantPlan } from "@/services/types";
import { apiErrorMessage } from "@/utils/errors";

// ── helpers ───────────────────────────────────────────────────────

const PLAN_LABEL: Record<TenantPlan, string> = {
  trial:        "Trial",
  starter:      "Starter",
  professional: "Professional",
  enterprise:   "Enterprise",
};

const PLAN_COLOR: Record<TenantPlan, string> = {
  trial:        "bg-gray-700 text-gray-300",
  starter:      "bg-blue-500/20 text-blue-300",
  professional: "bg-brand-500/20 text-brand-300",
  enterprise:   "bg-purple-500/20 text-purple-300",
};

const ROLE_LABEL: Record<string, string> = {
  operator:   "Operador",
  manager:    "Gestor",
  admin:      "Admin",
  superadmin: "Super Admin",
};

function PlanBadge({ plan }: { plan: TenantPlan }) {
  return (
    <span className={clsx("rounded-full px-2.5 py-0.5 text-xs font-medium", PLAN_COLOR[plan])}>
      {PLAN_LABEL[plan]}
    </span>
  );
}

function StatusBadge({ active }: { active: boolean }) {
  return (
    <span className={clsx(
      "flex items-center gap-1.5 text-xs font-medium",
      active ? "text-green-400" : "text-red-400"
    )}>
      <span className={clsx("size-1.5 rounded-full", active ? "bg-green-400 animate-pulse" : "bg-red-400")} />
      {active ? "Ativa" : "Suspensa"}
    </span>
  );
}

// ── Modal base ────────────────────────────────────────────────────

function Modal({ title, onClose, children }: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-lg rounded-xl border border-gray-700 bg-gray-900 shadow-2xl">
        <div className="flex items-center justify-between border-b border-gray-800 px-5 py-4">
          <h2 className="text-sm font-semibold text-gray-100">{title}</h2>
          <button onClick={onClose} className="text-fg-muted hover:text-gray-300">
            <svg viewBox="0 0 20 20" fill="currentColor" className="size-5">
              <path d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22z" />
            </svg>
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

// ── Create Tenant Modal ───────────────────────────────────────────

function CreateTenantModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({
    name: "", slug: "", plan: "trial" as TenantPlan,
    trial_ends_at: "", max_users: "5", max_machines: "10",
    admin_name: "", admin_email: "", admin_password: "",
  });
  const [error, setError] = useState("");

  const create = useMutation({
    mutationFn: () => adminApi.createTenant({
      ...form,
      max_users: Number(form.max_users),
      max_machines: Number(form.max_machines),
      trial_ends_at: form.trial_ends_at || null,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["admin", "tenants"] }); onClose(); },
    onError: (e: unknown) => setError(apiErrorMessage(e, "Erro ao criar empresa.")),
  });

  function field(key: keyof typeof form) {
    return {
      value: form[key],
      onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
        setForm((f) => ({ ...f, [key]: e.target.value })),
    };
  }

  // auto-gera slug a partir do nome
  function handleNameChange(e: React.ChangeEvent<HTMLInputElement>) {
    const name = e.target.value;
    setForm((f) => ({
      ...f,
      name,
      slug: f.slug || name.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, ""),
    }));
  }

  return (
    <Modal title="Nova Empresa" onClose={onClose}>
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2">
            <label className="block text-xs text-gray-400 mb-1">Nome da empresa</label>
            <input
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
              placeholder="Acme Indústria Ltda"
              value={form.name}
              onChange={handleNameChange}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Slug (URL)</label>
            <input
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 font-mono focus:border-brand-500 focus:outline-none"
              placeholder="acme-industria"
              {...field("slug")}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Plano</label>
            <select
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
              {...field("plan")}
            >
              <option value="trial">Trial</option>
              <option value="starter">Starter</option>
              <option value="professional">Professional</option>
              <option value="enterprise">Enterprise</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Expira em (trial)</label>
            <input type="date" className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none" {...field("trial_ends_at")} />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Máx. usuários</label>
            <input type="number" min={1} className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none" {...field("max_users")} />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Máx. máquinas</label>
            <input type="number" min={1} className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none" {...field("max_machines")} />
          </div>
        </div>

        <div className="border-t border-gray-800 pt-4">
          <p className="mb-3 text-xs font-medium text-gray-400">Administrador inicial</p>
          <div className="space-y-3">
            <input
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
              placeholder="Nome completo"
              {...field("admin_name")}
            />
            <input
              type="email"
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
              placeholder="email@empresa.com"
              {...field("admin_email")}
            />
            <input
              type="password"
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
              placeholder="Senha (mín. 12 caracteres)"
              {...field("admin_password")}
            />
          </div>
        </div>

        {error && <p className="text-xs text-red-400">{error}</p>}

        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} className="rounded-lg px-4 py-2 text-sm text-gray-400 hover:bg-gray-800">
            Cancelar
          </button>
          <button
            onClick={() => create.mutate()}
            disabled={create.isPending || !form.name || !form.slug || !form.admin_email || !form.admin_password}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent-hover disabled:opacity-50"
          >
            {create.isPending ? "Criando..." : "Criar Empresa"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ── Edit Tenant Modal ─────────────────────────────────────────────

function EditTenantModal({ tenant, onClose }: { tenant: TenantWithStats; onClose: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({
    name:             tenant.name,
    plan:             tenant.plan,
    max_users:        String(tenant.max_users),
    max_machines:     String(tenant.max_machines),
    trial_ends_at:    tenant.trial_ends_at ? tenant.trial_ends_at.split("T")[0] : "",
    suspended_reason: tenant.suspended_reason ?? "",
    is_active:        tenant.is_active,
  });
  const [error, setError] = useState("");

  const update = useMutation({
    mutationFn: () => adminApi.updateTenant(tenant.id, {
      name:             form.name,
      plan:             form.plan,
      max_users:        Number(form.max_users),
      max_machines:     Number(form.max_machines),
      trial_ends_at:    form.trial_ends_at || null,
      suspended_reason: form.suspended_reason || null,
      is_active:        form.is_active,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["admin", "tenants"] }); onClose(); },
    onError: (e: unknown) => setError(apiErrorMessage(e, "Erro ao salvar.")),
  });

  function field(key: keyof typeof form) {
    return {
      value: form[key] as string,
      onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
        setForm((f) => ({ ...f, [key]: e.target.value })),
    };
  }

  return (
    <Modal title={`Editar — ${tenant.name}`} onClose={onClose}>
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2">
            <label className="block text-xs text-gray-400 mb-1">Nome</label>
            <input className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none" {...field("name")} />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Plano</label>
            <select className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none" {...field("plan")}>
              <option value="trial">Trial</option>
              <option value="starter">Starter</option>
              <option value="professional">Professional</option>
              <option value="enterprise">Enterprise</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Status</label>
            <select
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
              value={form.is_active ? "1" : "0"}
              onChange={(e) => setForm((f) => ({ ...f, is_active: e.target.value === "1" }))}
            >
              <option value="1">Ativa</option>
              <option value="0">Suspensa</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Expira em (trial)</label>
            <input type="date" className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none" {...field("trial_ends_at")} />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Máx. usuários</label>
            <input type="number" min={1} className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none" {...field("max_users")} />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Máx. máquinas</label>
            <input type="number" min={1} className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none" {...field("max_machines")} />
          </div>
          {!form.is_active && (
            <div className="col-span-2">
              <label className="block text-xs text-gray-400 mb-1">Motivo da suspensão</label>
              <input className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none" placeholder="Ex: inadimplência, solicitação do cliente..." {...field("suspended_reason")} />
            </div>
          )}
        </div>

        {error && <p className="text-xs text-red-400">{error}</p>}

        <div className="flex justify-end gap-3 pt-1">
          <button onClick={onClose} className="rounded-lg px-4 py-2 text-sm text-gray-400 hover:bg-gray-800">Cancelar</button>
          <button
            onClick={() => update.mutate()}
            disabled={update.isPending}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent-hover disabled:opacity-50"
          >
            {update.isPending ? "Salvando..." : "Salvar"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ── Users Panel ───────────────────────────────────────────────────

function UsersPanel({ tenant, onClose }: { tenant: TenantWithStats; onClose: () => void }) {
  const qc = useQueryClient();
  const [showAdd, setShowAdd] = useState(false);
  const [newUser, setNewUser] = useState({ name: "", email: "", password: "", role: "operator" });
  const [addError, setAddError] = useState("");

  const { data: users = [], isLoading, isError: usersError, error: usersErr, refetch: refetchUsers } = useQuery<TenantUser[]>({
    queryKey: ["admin", "tenants", tenant.id, "users"],
    queryFn: () => adminApi.listUsers(tenant.id).then((r) => r.data),
  });

  const createUser = useMutation({
    mutationFn: () => adminApi.createUser(tenant.id, newUser),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "tenants", tenant.id, "users"] });
      qc.invalidateQueries({ queryKey: ["admin", "tenants"] });
      setShowAdd(false);
      setNewUser({ name: "", email: "", password: "", role: "operator" });
    },
    onError: (e: unknown) => setAddError(apiErrorMessage(e, "Erro ao criar usuário.")),
  });

  const toggleUser = useMutation({
    mutationFn: ({ userId, is_active }: { userId: string; is_active: boolean }) =>
      adminApi.updateUser(tenant.id, userId, { is_active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "tenants", tenant.id, "users"] }),
  });

  const deleteUser = useMutation({
    mutationFn: (userId: string) => adminApi.deleteUser(tenant.id, userId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "tenants", tenant.id, "users"] });
      qc.invalidateQueries({ queryKey: ["admin", "tenants"] });
    },
  });

  return (
    <Modal title={`Usuários — ${tenant.name}`} onClose={onClose}>
      <div className="space-y-4">
        {isLoading ? (
          <div className="flex justify-center py-8">
            <span className="size-5 animate-spin rounded-full border-2 border-brand-500 border-t-transparent" />
          </div>
        ) : usersError ? (
          <ErrorState title="Não foi possível carregar os usuários" error={usersErr} onRetry={() => void refetchUsers()} />
        ) : (
          <div className="rounded-lg border border-gray-800 overflow-hidden">
            {users.length === 0 ? (
              <p className="px-4 py-6 text-center text-sm text-fg-muted">Nenhum usuário cadastrado.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-800 bg-gray-800/50">
                    {["Nome", "E-mail", "Papel", "Status", ""].map((h) => (
                      <th key={h} className="px-3 py-2 text-left text-xs text-fg-muted font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {users.map((u, i) => (
                    <tr key={u.id} className={clsx("hover:bg-gray-800/30", i < users.length - 1 && "border-b border-gray-800/40")}>
                      <td className="px-3 py-2.5 text-gray-200 font-medium">{u.name}</td>
                      <td className="px-3 py-2.5 text-gray-400 font-mono text-xs">{u.email}</td>
                      <td className="px-3 py-2.5">
                        <span className="rounded-full bg-gray-700 px-2 py-0.5 text-xs text-gray-300">
                          {ROLE_LABEL[u.role] ?? u.role}
                        </span>
                      </td>
                      <td className="px-3 py-2.5">
                        <span className={clsx("text-xs font-medium", u.is_active ? "text-green-400" : "text-red-400")}>
                          {u.is_active ? "Ativo" : "Inativo"}
                        </span>
                      </td>
                      <td className="px-3 py-2.5">
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => toggleUser.mutate({ userId: u.id, is_active: !u.is_active })}
                            className="text-xs text-fg-muted hover:text-gray-300"
                            title={u.is_active ? "Desativar" : "Ativar"}
                          >
                            {u.is_active ? "Desativar" : "Ativar"}
                          </button>
                          <button
                            onClick={() => { if (confirm(`Remover ${u.name}?`)) deleteUser.mutate(u.id); }}
                            className="text-xs text-red-500/70 hover:text-red-400"
                          >
                            Remover
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        {showAdd ? (
          <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-4 space-y-3">
            <p className="text-xs font-medium text-gray-400">Novo usuário</p>
            <input className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none" placeholder="Nome" value={newUser.name} onChange={(e) => setNewUser((u) => ({ ...u, name: e.target.value }))} />
            <input type="email" className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none" placeholder="E-mail" value={newUser.email} onChange={(e) => setNewUser((u) => ({ ...u, email: e.target.value }))} />
            <input type="password" className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none" placeholder="Senha" value={newUser.password} onChange={(e) => setNewUser((u) => ({ ...u, password: e.target.value }))} />
            <select className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none" value={newUser.role} onChange={(e) => setNewUser((u) => ({ ...u, role: e.target.value }))}>
              <option value="operator">Operador</option>
              <option value="manager">Gestor</option>
              <option value="admin">Administrador</option>
            </select>
            {addError && <p className="text-xs text-red-400">{addError}</p>}
            <div className="flex gap-2">
              <button onClick={() => { setShowAdd(false); setAddError(""); }} className="rounded-lg px-3 py-1.5 text-xs text-gray-400 hover:bg-gray-700">Cancelar</button>
              <button onClick={() => createUser.mutate()} disabled={createUser.isPending} className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg hover:bg-accent-hover disabled:opacity-50">
                {createUser.isPending ? "Salvando..." : "Adicionar"}
              </button>
            </div>
          </div>
        ) : (
          <button
            onClick={() => setShowAdd(true)}
            disabled={users.length >= tenant.max_users}
            className="flex items-center gap-2 rounded-lg border border-dashed border-gray-700 px-4 py-2.5 text-sm text-fg-muted hover:border-gray-600 hover:text-gray-300 disabled:opacity-40 disabled:cursor-not-allowed w-full justify-center"
          >
            <svg viewBox="0 0 20 20" fill="currentColor" className="size-4">
              <path d="M10.75 4.75a.75.75 0 0 0-1.5 0v4.5h-4.5a.75.75 0 0 0 0 1.5h4.5v4.5a.75.75 0 0 0 1.5 0v-4.5h4.5a.75.75 0 0 0 0-1.5h-4.5v-4.5z" />
            </svg>
            {users.length >= tenant.max_users ? `Limite atingido (${tenant.max_users})` : "Adicionar usuário"}
          </button>
        )}

        <div className="flex justify-end pt-1">
          <button onClick={onClose} className="rounded-lg px-4 py-2 text-sm text-gray-400 hover:bg-gray-800">Fechar</button>
        </div>
      </div>
    </Modal>
  );
}

// ── Main Page ─────────────────────────────────────────────────────

export default function AdminPage() {
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing]       = useState<TenantWithStats | null>(null);
  const [viewUsers, setViewUsers]   = useState<TenantWithStats | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<TenantWithStats | null>(null);
  const qc = useQueryClient();

  const { data: tenants = [], isLoading, isError: tenantsError, error: tenantsErr, refetch: refetchTenants } = useQuery<TenantWithStats[]>({
    queryKey: ["admin", "tenants"],
    queryFn: () => adminApi.listTenants().then((r) => r.data),
    refetchInterval: 30_000,
  });

  const deleteTenant = useMutation({
    mutationFn: (id: string) => adminApi.deleteTenant(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["admin", "tenants"] }); setConfirmDelete(null); },
  });

  const active   = tenants.filter((t) => t.is_active).length;
  const trial    = tenants.filter((t) => t.plan === "trial").length;
  const suspended = tenants.filter((t) => !t.is_active).length;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-gray-100">Painel de Administração</h1>
          <p className="text-sm text-fg-muted">Gerencie empresas e usuários da plataforma</p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent-hover"
        >
          <svg viewBox="0 0 20 20" fill="currentColor" className="size-4">
            <path d="M10.75 4.75a.75.75 0 0 0-1.5 0v4.5h-4.5a.75.75 0 0 0 0 1.5h4.5v4.5a.75.75 0 0 0 1.5 0v-4.5h4.5a.75.75 0 0 0 0-1.5h-4.5v-4.5z" />
          </svg>
          Nova Empresa
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: "Total",     value: tenants.length, color: "text-gray-100" },
          { label: "Ativas",    value: active,         color: "text-green-400" },
          { label: "Trial",     value: trial,          color: "text-yellow-400" },
          { label: "Suspensas", value: suspended,      color: "text-red-400" },
        ].map((s) => (
          <div key={s.label} className="rounded-xl border border-gray-800 bg-gray-900 px-5 py-4">
            <p className="text-xs text-fg-muted">{s.label}</p>
            <p className={clsx("mt-1 text-2xl font-bold font-mono", s.color)}>{s.value}</p>
          </div>
        ))}
      </div>

      {/* Table */}
      <div className="rounded-xl border border-gray-800 bg-gray-900 overflow-hidden">
        {isLoading ? (
          <div className="flex justify-center py-16">
            <span className="size-6 animate-spin rounded-full border-2 border-brand-500 border-t-transparent" />
          </div>
        ) : tenantsError ? (
          <ErrorState className="m-4" title="Não foi possível carregar as empresas" error={tenantsErr} onRetry={() => void refetchTenants()} />
        ) : tenants.length === 0 ? (
          <div className="py-16 text-center">
            <p className="text-sm text-fg-muted">Nenhuma empresa cadastrada.</p>
            <button onClick={() => setShowCreate(true)} className="mt-3 text-sm text-brand-400 hover:text-brand-300">
              Criar a primeira empresa
            </button>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 bg-gray-800/30">
                {["Empresa", "Plano", "Status", "Usuários", "Máquinas", "Criada em", "Ações"].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs text-fg-muted font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tenants.map((t, i) => (
                <tr key={t.id} className={clsx("hover:bg-gray-800/20 transition-colors", i < tenants.length - 1 && "border-b border-gray-800/40")}>
                  <td className="px-4 py-3">
                    <p className="font-medium text-gray-100">{t.name}</p>
                    <p className="text-xs text-fg-muted font-mono">{t.slug}</p>
                  </td>
                  <td className="px-4 py-3">
                    <PlanBadge plan={t.plan} />
                    {t.trial_ends_at && (
                      <p className="mt-0.5 text-xs text-fg-muted">
                        até {formatDate(t.trial_ends_at)}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge active={t.is_active} />
                    {t.suspended_reason && (
                      <p className="mt-0.5 text-xs text-fg-muted max-w-[140px] truncate" title={t.suspended_reason}>
                        {t.suspended_reason}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span className={clsx("font-mono text-xs", t.user_count >= t.max_users ? "text-yellow-400" : "text-gray-300")}>
                      {t.user_count}/{t.max_users}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className={clsx("font-mono text-xs", t.machine_count >= t.max_machines ? "text-yellow-400" : "text-gray-300")}>
                      {t.machine_count}/{t.max_machines}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-fg-muted">
                    {formatDate(t.created_at)}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-3">
                      <button
                        onClick={() => setViewUsers(t)}
                        className="text-xs text-gray-400 hover:text-gray-200"
                        title="Usuários"
                      >
                        Usuários
                      </button>
                      <button
                        onClick={() => setEditing(t)}
                        className="text-xs text-brand-400 hover:text-brand-300"
                      >
                        Editar
                      </button>
                      <button
                        onClick={() => setConfirmDelete(t)}
                        className="text-xs text-red-500/70 hover:text-red-400"
                      >
                        Excluir
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Modals */}
      {showCreate && <CreateTenantModal onClose={() => setShowCreate(false)} />}
      {editing    && <EditTenantModal tenant={editing} onClose={() => setEditing(null)} />}
      {viewUsers  && <UsersPanel tenant={viewUsers} onClose={() => setViewUsers(null)} />}

      {confirmDelete && (
        <Modal title="Confirmar exclusão" onClose={() => setConfirmDelete(null)}>
          <div className="space-y-4">
            <p className="text-sm text-gray-300">
              Tem certeza que deseja excluir a empresa{" "}
              <span className="font-semibold text-gray-100">{confirmDelete.name}</span>?
            </p>
            <p className="text-xs text-red-400">
              Esta ação é irreversível. Todos os dados (usuários, linhas, máquinas, sensores) serão removidos.
            </p>
            <div className="flex justify-end gap-3">
              <button onClick={() => setConfirmDelete(null)} className="rounded-lg px-4 py-2 text-sm text-gray-400 hover:bg-gray-800">Cancelar</button>
              <button
                onClick={() => deleteTenant.mutate(confirmDelete.id)}
                disabled={deleteTenant.isPending}
                className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
              >
                {deleteTenant.isPending ? "Excluindo..." : "Excluir"}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
