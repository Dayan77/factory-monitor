import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { isAxiosError } from "axios";
import AuthShell, { AuthNotice, PasswordInput } from "@/components/auth/AuthShell";
import Button from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { authFlowApi } from "@/services/accountApi";
import { useUrlToken } from "@/hooks/useUrlToken";
import { apiErrorMessage } from "@/utils/errors";
import { PASSWORD_MIN, checkPassword } from "@/utils/password";

/** Target of the e-mailed link (`/redefinir-senha?token=…`): a new password ends every open session. */
export default function ResetPasswordPage() {
  const token = useUrlToken();
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [invalidLink, setInvalidLink] = useState(!token);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    const c = checkPassword(password);
    if (!c.ok) { setError(c.problem ?? ""); return; }
    if (password !== confirm) { setError("As senhas não coincidem."); return; }
    setBusy(true);
    try {
      await authFlowApi.confirmReset(token, password);
      navigate("/login?reset=1", { replace: true });
    } catch (err) {
      if (isAxiosError(err) && err.response?.status === 400) setInvalidLink(true);
      else setError(apiErrorMessage(err, "Não foi possível redefinir a senha."));
    } finally {
      setBusy(false);
    }
  }

  if (invalidLink) {
    return (
      <AuthShell title="Link inválido ou expirado">
        <div className="space-y-4" data-testid="reset-invalid">
          <AuthNotice tone="error">Este link de redefinição não vale mais (ele expira em 15 minutos e só pode ser usado uma vez).</AuthNotice>
          <Link to="/esqueci-senha" className="inline-flex min-h-12 w-full items-center justify-center rounded-lg bg-accent px-5 text-body font-medium text-accent-fg hover:bg-accent-hover">
            Pedir um novo link
          </Link>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Criar nova senha" subtitle="Ao salvar, todas as sessões abertas da sua conta são encerradas.">
      <form onSubmit={submit} className="space-y-4" data-testid="reset-form">
        <Field label="Nova senha" htmlFor="reset-pass" hint={`Mínimo ${PASSWORD_MIN} caracteres. Evite seu nome, e-mail ou senhas já usadas.`}>
          <PasswordInput id="reset-pass" value={password} onChange={setPassword} autoComplete="new-password" meter />
        </Field>
        <Field label="Confirme a nova senha" htmlFor="reset-pass2">
          <PasswordInput id="reset-pass2" value={confirm} onChange={setConfirm} autoComplete="new-password" />
        </Field>
        {error && <AuthNotice tone="error">{error}</AuthNotice>}
        <Button type="submit" variant="primary" size="lg" loading={busy} demoSafe className="w-full">Salvar nova senha</Button>
      </form>
    </AuthShell>
  );
}
