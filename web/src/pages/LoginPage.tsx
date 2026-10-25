import { FormEvent, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { isAxiosError } from "axios";
import { useAuthStore } from "@/store/auth";
import { can, hasRole } from "@/utils/maintenance";
import { apiErrorMessage } from "@/utils/errors";
import { normalizeOtp } from "@/utils/password";
import { SESSION_EXPIRED_KEY, safeNext } from "@/services/api";
import AuthShell, { AuthNotice, PasswordInput } from "@/components/auth/AuthShell";
import Button from "@/components/ui/Button";
import { Field, inputClass } from "@/components/ui/Field";

function landingFor(next: string | null): string {
  if (next) return next;
  // technicians land on their phone app (Minhas OS); everyone else on the Raio-X
  const u = useAuthStore.getState().user;
  const techOnly = hasRole(u, "technician") && !can(u, "maintenance.plan") && !hasRole(u, "production_supervisor", "operator");
  return techOnly ? "/m/work-orders" : "/dashboard";
}

function loginError(err: unknown): string {
  if (isAxiosError(err) && !err.response) return "Sem conexão com o servidor. Verifique a rede e tente de novo.";
  if (isAxiosError(err) && err.response?.status === 401) return "E-mail ou senha incorretos.";
  return apiErrorMessage(err, "Não foi possível entrar. Tente novamente.");
}

// pure read (StrictMode runs state initializers twice); the flag is cleared in an effect
function readExpired(): boolean {
  try { return sessionStorage.getItem(SESSION_EXPIRED_KEY) === "1"; } catch { return false; }
}

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [useRecovery, setUseRecovery] = useState(false);
  const [code, setCode] = useState("");
  const [expired] = useState(readExpired);
  const codeRef = useRef<HTMLInputElement>(null);

  const login = useAuthStore((s) => s.login);
  const verifyMfa = useAuthStore((s) => s.verifyMfa);
  const loading = useAuthStore((s) => s.loading);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const justRegistered = searchParams.get("registered") === "1";
  const passwordReset = searchParams.get("reset") === "1";
  const deleted = searchParams.get("deleted") === "1";
  const next = safeNext(searchParams.get("next"));

  useEffect(() => { if (mfaToken) codeRef.current?.focus(); }, [mfaToken, useRecovery]);
  useEffect(() => { try { sessionStorage.removeItem(SESSION_EXPIRED_KEY); } catch { /* storage blocked */ } }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const r = await login(email.trim(), password);
      if (r.mfa) {
        setMfaToken(r.mfaToken);
        setCode("");
        return;
      }
      navigate(landingFor(next), { replace: true });
    } catch (err) {
      setError(loginError(err));
    }
  }

  async function handleMfa(e: FormEvent) {
    e.preventDefault();
    if (!mfaToken) return;
    setError("");
    const c = normalizeOtp(code);
    try {
      await verifyMfa(mfaToken, useRecovery ? { recovery_code: c } : { code: c });
      navigate(landingFor(next), { replace: true });
    } catch (err) {
      const msg = apiErrorMessage(err, "Código inválido.");
      // the 5-minute challenge expired or was used: start over from the password
      if (isAxiosError(err) && err.response?.status === 401 && /expirad/i.test(msg)) {
        setMfaToken(null);
        setPassword("");
      }
      setError(msg);
    }
  }

  if (mfaToken) {
    return (
      <AuthShell title="Verificação em duas etapas" subtitle={useRecovery
        ? "Digite um dos códigos de recuperação que você guardou ao ativar a verificação."
        : "Abra o aplicativo autenticador e digite o código de 6 dígitos."}>
        <form onSubmit={handleMfa} className="space-y-4" data-testid="mfa-step">
          <Field label={useRecovery ? "Código de recuperação" : "Código do autenticador"} htmlFor="mfa-code">
            <input
              ref={codeRef}
              id="mfa-code"
              inputMode={useRecovery ? "text" : "numeric"}
              autoComplete="one-time-code"
              pattern={useRecovery ? undefined : "[0-9 ]{6,7}"}
              maxLength={useRecovery ? 32 : 7}
              required
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder={useRecovery ? "xxxxx-xxxxx" : "000000"}
              className={`${inputClass} text-center font-mono text-title tracking-[0.3em]`}
            />
          </Field>
          {error && <AuthNotice tone="error">{error}</AuthNotice>}
          <Button type="submit" variant="primary" size="lg" loading={loading} demoSafe className="w-full">Verificar</Button>
          <div className="flex flex-wrap justify-between gap-2 text-caption">
            <button type="button" className="font-medium text-ok underline-offset-2 hover:underline"
              onClick={() => { setUseRecovery((v) => !v); setCode(""); setError(""); }}>
              {useRecovery ? "Usar o código do autenticador" : "Usar um código de recuperação"}
            </button>
            <button type="button" className="text-fg-muted underline-offset-2 hover:text-fg hover:underline"
              onClick={() => { setMfaToken(null); setPassword(""); setError(""); }}>
              Voltar
            </button>
          </div>
        </form>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title="Acesse sua conta"
      footer={<p>Não tem conta? <Link to="/register" className="font-medium text-ok underline-offset-2 hover:underline">Criar gratuitamente</Link></p>}
    >
      <form onSubmit={handleSubmit} className="space-y-4" data-testid="login-form">
        {expired && <AuthNotice tone="info" testId="session-expired">Sua sessão expirou. Entre novamente para continuar de onde parou.</AuthNotice>}
        {justRegistered && <AuthNotice tone="ok">Conta criada com sucesso! Faça login para continuar.</AuthNotice>}
        {passwordReset && <AuthNotice tone="ok">Senha redefinida. Entre com a nova senha.</AuthNotice>}
        {deleted && <AuthNotice tone="info">Sua conta foi excluída. Obrigado por usar o FactoryConnect.</AuthNotice>}
        <Field label="E-mail" htmlFor="login-email">
          <input id="login-email" type="email" autoComplete="username" required value={email}
            onChange={(e) => setEmail(e.target.value)} placeholder="seu@email.com" className={inputClass} />
        </Field>
        <div className="space-y-1.5">
          <div className="flex items-baseline justify-between gap-2">
            <label htmlFor="login-password" className="block text-caption font-medium text-fg-muted">Senha</label>
            <Link to="/esqueci-senha" className="text-caption font-medium text-ok underline-offset-2 hover:underline">Esqueci minha senha</Link>
          </div>
          <PasswordInput id="login-password" value={password} onChange={setPassword} autoComplete="current-password" />
        </div>
        {error && <AuthNotice tone="error">{error}</AuthNotice>}
        <Button type="submit" variant="primary" size="lg" loading={loading} demoSafe className="w-full">
          {loading ? "Entrando…" : "Entrar"}
        </Button>
      </form>
    </AuthShell>
  );
}
