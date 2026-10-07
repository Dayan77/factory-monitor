import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { isAxiosError } from "axios";
import AuthShell, { AuthNotice } from "@/components/auth/AuthShell";
import Button from "@/components/ui/Button";
import { Field, inputClass } from "@/components/ui/Field";
import { authFlowApi } from "@/services/accountApi";
import { apiErrorMessage } from "@/utils/errors";

/**
 * "Esqueci minha senha": always the same answer whether or not the e-mail exists (the server does not reveal
 * which accounts exist — docs/security.md); the link is valid for 15 minutes and only once.
 */
export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await authFlowApi.requestReset(email.trim());
      setSent(true);
    } catch (err) {
      setError(isAxiosError(err) && !err.response
        ? "Sem conexão com o servidor. Tente novamente."
        : apiErrorMessage(err, "Não foi possível enviar agora. Tente novamente em alguns minutos."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title="Redefinir senha" subtitle="Enviaremos um link para criar uma nova senha."
      footer={<p><Link to="/login" className="font-medium text-ok underline-offset-2 hover:underline">Voltar para o login</Link></p>}>
      {sent ? (
        <div className="space-y-3" data-testid="reset-sent">
          <AuthNotice tone="ok">
            Se houver uma conta com <strong className="break-all">{email.trim()}</strong>, você receberá um e-mail com o link em instantes.
          </AuthNotice>
          <p className="text-body text-fg-muted">O link vale por 15 minutos e uma única vez. Não chegou? Confira o spam ou peça de novo.</p>
          <Button variant="secondary" className="w-full" onClick={() => setSent(false)}>Enviar novamente</Button>
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <Field label="E-mail da sua conta" htmlFor="reset-email">
            <input id="reset-email" type="email" autoComplete="email" required value={email}
              onChange={(e) => setEmail(e.target.value)} className={inputClass} placeholder="seu@email.com" />
          </Field>
          {error && <AuthNotice tone="error">{error}</AuthNotice>}
          <Button type="submit" variant="primary" size="lg" loading={busy} demoSafe className="w-full">Enviar link</Button>
        </form>
      )}
    </AuthShell>
  );
}
