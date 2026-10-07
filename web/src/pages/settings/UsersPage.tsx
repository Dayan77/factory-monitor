import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import Button from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Field, inputClass } from "@/components/ui/Field";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import ErrorState from "@/components/ui/ErrorState";
import EmptyState from "@/components/ui/EmptyState";
import CopyButton from "@/components/ui/CopyButton";
import { usersApi, type Invitation } from "@/services/accountApi";
import { useLines } from "@/hooks/useQueries";
import { apiErrorMessage } from "@/utils/errors";
import { formatDateTime } from "@/utils/format";
import { ASSIGNABLE_ROLES, ROLE_LABELS, roleList } from "@/utils/roles";

const STATE: Record<Invitation["state"], { label: string; cls: string }> = {
  pending: { label: "Pendente", cls: "bg-info-solid/15 text-info" },
  accepted: { label: "Aceito", cls: "bg-ok-solid/15 text-ok" },
  revoked: { label: "Revogado", cls: "bg-neutral-solid/15 text-fg-muted" },
  expired: { label: "Expirado", cls: "bg-warn-solid/15 text-warn" },
};

const LINE_ROLES = new Set(["operator", "production_supervisor"]);

/** Users of the tenant and invitations (admin). The invitee sets their own password via the e-mailed link. */
export default function UsersPage() {
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: () => usersApi.list().then((r) => r.data) });
  const invites = useQuery({ queryKey: ["invitations"], queryFn: () => usersApi.invitations().then((r) => r.data) });
  const lines = useLines();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [roles, setRoles] = useState<string[]>(["operator"]);
  const [lineIds, setLineIds] = useState<string[]>([]);
  const [lastLink, setLastLink] = useState<{ email: string; url: string } | null>(null);
  const [clientError, setClientError] = useState("");

  const invite = useMutation({
    mutationFn: () => {
      // primary role: the most privileged one picked (the server keeps `role` for compatibility)
      const primary = [...ASSIGNABLE_ROLES].reverse().find((r) => roles.includes(r)) ?? "operator";
      return usersApi.invite({
        email: email.trim(), name: name.trim() || undefined, role: primary, roles,
        line_ids: roles.some((r) => LINE_ROLES.has(r)) ? lineIds : [],
      }).then((r) => r.data);
    },
    onSuccess: (inv) => {
      setLastLink(inv.invite_url ? { email: inv.email, url: inv.invite_url } : null);
      setEmail(""); setName(""); setRoles(["operator"]); setLineIds([]);
      void qc.invalidateQueries({ queryKey: ["invitations"] });
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) => usersApi.revokeInvitation(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["invitations"] }),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setClientError("");
    if (roles.length === 0) { setClientError("Escolha ao menos um papel."); return; }
    invite.mutate();
  }

  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const needsLines = roles.some((r) => LINE_ROLES.has(r));

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <Card>
        <CardHeader title="Convidar pessoa" description="A pessoa recebe um link por e-mail (válido por 7 dias) e cria a própria senha. Você nunca vê nem define a senha de ninguém." />
        <form className="grid gap-4 sm:grid-cols-2" onSubmit={submit} data-testid="invite-user-form">
          <Field label="E-mail" htmlFor="inv-email">
            <input id="inv-email" type="email" required autoComplete="off" value={email} onChange={(e) => setEmail(e.target.value)} className={inputClass} />
          </Field>
          <Field label="Nome (opcional)" htmlFor="inv-name-admin">
            <input id="inv-name-admin" autoComplete="off" value={name} onChange={(e) => setName(e.target.value)} className={inputClass} />
          </Field>
          <fieldset className="sm:col-span-2">
            <legend className="mb-1.5 text-caption font-medium text-fg-muted">Papéis</legend>
            <div className="flex flex-wrap gap-2">
              {ASSIGNABLE_ROLES.map((r) => (
                <label key={r} className={clsx("flex min-h-10 cursor-pointer items-center gap-2 rounded-lg border px-3 text-body",
                  roles.includes(r) ? "border-accent bg-accent/10 text-fg" : "border-line-strong text-fg-muted")}>
                  <input type="checkbox" className="size-4 accent-[rgb(var(--c-accent))]" checked={roles.includes(r)} onChange={() => setRoles(toggle(roles, r))} />
                  {ROLE_LABELS[r]}
                </label>
              ))}
            </div>
          </fieldset>
          {needsLines && (
            <fieldset className="sm:col-span-2">
              <legend className="mb-1.5 text-caption font-medium text-fg-muted">Linhas da área (operador / supervisor só atuam nelas; nenhuma = todas)</legend>
              {lines.isPending ? <Skeleton className="h-10" /> : lines.isError ? <ErrorState error={lines.error} /> : (lines.data ?? []).length === 0 ? (
                <p className="text-caption text-fg-muted">Nenhuma linha cadastrada.</p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {(lines.data ?? []).map((l) => (
                    <label key={l.id} className="flex min-h-10 cursor-pointer items-center gap-2 rounded-lg border border-line-strong px-3 text-body text-fg">
                      <input type="checkbox" className="size-4 accent-[rgb(var(--c-accent))]" checked={lineIds.includes(l.id)} onChange={() => setLineIds(toggle(lineIds, l.id))} />
                      {l.name}
                    </label>
                  ))}
                </div>
              )}
            </fieldset>
          )}
          <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
            <Button type="submit" variant="primary" loading={invite.isPending}>Enviar convite</Button>
            {(clientError || invite.isError) && <p role="alert" className="text-caption text-danger">{clientError || apiErrorMessage(invite.error, "Não foi possível convidar.")}</p>}
          </div>
        </form>
        {lastLink && (
          <div role="status" className="mt-4 space-y-2 rounded-lg border border-ok-solid/40 bg-ok-solid/10 p-3" data-testid="invite-link">
            <p className="text-body text-fg">Convite enviado para <strong className="break-all">{lastLink.email}</strong>. Se o e-mail não chegar, compartilhe o link abaixo por um canal seguro (ele só aparece agora):</p>
            <div className="flex flex-wrap items-center gap-2">
              <code className="min-w-0 flex-1 break-all rounded bg-surface px-2 py-1 font-mono text-caption text-fg">{lastLink.url}</code>
              <CopyButton text={lastLink.url} label="Copiar link" />
            </div>
          </div>
        )}
      </Card>

      <Card>
        <CardHeader title="Convites" />
        {invites.isPending ? <LoadingRegion><Skeleton className="h-12" /><Skeleton className="mt-2 h-12" /></LoadingRegion>
          : invites.isError ? <ErrorState error={invites.error} onRetry={() => void invites.refetch()} />
          : invites.data.length === 0 ? <EmptyState title="Nenhum convite ainda" description="Os convites enviados aparecem aqui até serem aceitos." />
          : (
            <ul className="divide-y divide-line">
              {invites.data.map((i) => (
                <li key={i.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                  <div className="min-w-0">
                    <p className="break-all text-body font-medium text-fg">{i.name ? `${i.name} · ` : ""}{i.email}</p>
                    <p className="text-caption text-fg-muted">{roleList(i)} · enviado {formatDateTime(i.created_at)} · expira {formatDateTime(i.expires_at)}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={clsx("rounded-full px-2 py-0.5 text-caption font-medium", STATE[i.state].cls)}>{STATE[i.state].label}</span>
                    {i.state === "pending" && (
                      <Button size="sm" variant="danger-ghost" loading={revoke.isPending && revoke.variables === i.id} onClick={() => revoke.mutate(i.id)}>
                        Revogar<span className="sr-only"> convite de {i.email}</span>
                      </Button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        {revoke.isError && <p role="alert" className="mt-2 text-caption text-danger">{apiErrorMessage(revoke.error, "Não foi possível revogar.")}</p>}
      </Card>

      <Card>
        <CardHeader title="Usuários" description="Pessoas com acesso ao FactoryConnect nesta empresa." />
        {users.isPending ? <LoadingRegion><Skeleton className="h-12" /><Skeleton className="mt-2 h-12" /></LoadingRegion>
          : users.isError ? <ErrorState error={users.error} onRetry={() => void users.refetch()} />
          : users.data.length === 0 ? <EmptyState title="Nenhum usuário" />
          : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-body">
                <caption className="sr-only">Usuários do tenant</caption>
                <thead className="text-caption text-fg-muted">
                  <tr><th scope="col" className="py-2 pr-4 font-medium">Nome</th><th scope="col" className="py-2 pr-4 font-medium">E-mail</th><th scope="col" className="py-2 pr-4 font-medium">Papéis</th><th scope="col" className="py-2 font-medium">Situação</th></tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {users.data.map((u) => (
                    <tr key={u.id}>
                      <td className="py-2 pr-4 text-fg">{u.name}</td>
                      <td className="break-all py-2 pr-4 text-fg-muted">{u.email}</td>
                      <td className="py-2 pr-4 text-fg-muted">{roleList(u)}</td>
                      <td className="py-2 text-fg-muted">{u.is_active === false ? "Inativo" : "Ativo"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </Card>
    </div>
  );
}
