import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import AuthShell, { AuthNotice, PasswordInput } from "@/components/auth/AuthShell";
import Button from "@/components/ui/Button";
import { Field, inputClass } from "@/components/ui/Field";
import { Skeleton } from "@/components/ui/Skeleton";
import { authFlowApi } from "@/services/accountApi";
import { useAuthStore } from "@/store/auth";
import { useUrlToken } from "@/hooks/useUrlToken";
import { apiErrorMessage } from "@/utils/errors";
import { PASSWORD_MIN, checkPassword } from "@/utils/password";
import { formatDateTime } from "@/utils/format";
import { ROLE_LABELS } from "@/utils/roles";

/** Target of the invitation e-mail (`/convite?token=…`): the invitee picks a name and a password and is signed in. */
export default function InvitePage() {
  const token = useUrlToken();
  const navigate = useNavigate();
  const signIn = useAuthStore((s) => s.signInWithTokens);
  const inv = useQuery({
    queryKey: ["invitation", token],
    queryFn: () => authFlowApi.invitation(token).then((r) => r.data),
    enabled: !!token,
    retry: false,
    staleTime: Infinity,
  });
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!inv.data) return;
    setError("");
    const finalName = (name || inv.data.name || "").trim();
    const c = checkPassword(password, [inv.data.email, finalName, inv.data.tenant_name]);
    if (!c.ok) { setError(c.problem ?? ""); return; }
    if (password !== confirm) { setError("As senhas não coincidem."); return; }
    setBusy(true);
    try {
      const r = await authFlowApi.acceptInvitation(token, password, finalName || undefined);
      await signIn(r.data);
      navigate("/dashboard", { replace: true });
    } catch (err) {
      setError(apiErrorMessage(err, "Não foi possível aceitar o convite."));
    } finally {
      setBusy(false);
    }
  }

  if (!token || inv.isError) {
    return (
      <AuthShell title="Convite inválido ou expirado">
        <div className="space-y-3" data-testid="invite-invalid">
          <AuthNotice tone="error">{token ? apiErrorMessage(inv.error, "Convite inválido ou expirado.") : "O link do convite está incompleto."}</AuthNotice>
          <p className="text-body text-fg-muted">Peça ao administrador da sua empresa para enviar um novo convite (eles valem 7 dias).</p>
          <Link to="/login" className="text-body font-medium text-ok underline-offset-2 hover:underline">Já tenho conta — entrar</Link>
        </div>
      </AuthShell>
    );
  }

  if (inv.isPending) {
    return (
      <AuthShell title="Aceitar convite">
        <div role="status" aria-busy="true" className="space-y-3">
          <span className="sr-only">Carregando convite…</span>
          <Skeleton className="h-5 w-3/4" /><Skeleton className="h-10" /><Skeleton className="h-10" />
        </div>
      </AuthShell>
    );
  }

  const d = inv.data;
  return (
    <AuthShell title="Aceitar convite" subtitle={<>Você foi convidado para usar o FactoryConnect{d.tenant_name ? <> em <strong className="text-fg">{d.tenant_name}</strong></> : null}.</>}>
      <form onSubmit={submit} className="space-y-4" data-testid="invite-form">
        <dl className="grid grid-cols-[auto,1fr] gap-x-3 gap-y-1 rounded-lg bg-surface-2 p-3 text-body">
          <dt className="text-fg-muted">E-mail</dt><dd className="break-all text-fg">{d.email}</dd>
          <dt className="text-fg-muted">Papel</dt><dd className="text-fg">{ROLE_LABELS[d.role] ?? d.role}</dd>
          <dt className="text-fg-muted">Válido até</dt><dd className="text-fg">{formatDateTime(d.expires_at)}</dd>
        </dl>
        <Field label="Seu nome" htmlFor="inv-name">
          <input id="inv-name" autoComplete="name" required value={name || d.name || ""} onChange={(e) => setName(e.target.value)} className={inputClass} />
        </Field>
        <Field label="Crie uma senha" htmlFor="inv-pass" hint={`Mínimo ${PASSWORD_MIN} caracteres, sem seu nome, e-mail ou o nome da empresa.`}>
          <PasswordInput id="inv-pass" value={password} onChange={setPassword} autoComplete="new-password" meter />
        </Field>
        <Field label="Confirme a senha" htmlFor="inv-pass2">
          <PasswordInput id="inv-pass2" value={confirm} onChange={setConfirm} autoComplete="new-password" />
        </Field>
        {error && <AuthNotice tone="error">{error}</AuthNotice>}
        <Button type="submit" variant="primary" size="lg" loading={busy} demoSafe className="w-full">Criar conta e entrar</Button>
      </form>
    </AuthShell>
  );
}
