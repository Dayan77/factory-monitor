import { FormEvent, useEffect, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import clsx from "clsx";
import Button from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Field, inputClass } from "@/components/ui/Field";
import { Skeleton, LoadingRegion } from "@/components/ui/Skeleton";
import ErrorState from "@/components/ui/ErrorState";
import EmptyState from "@/components/ui/EmptyState";
import Sheet from "@/components/ui/Sheet";
import CopyButton from "@/components/ui/CopyButton";
import { AuthNotice, PasswordInput } from "@/components/auth/AuthShell";
import { accountApi, type AccountMe, type AccountSession, type MfaEnrollment } from "@/services/accountApi";
import { useAuthStore } from "@/store/auth";
import { apiErrorMessage } from "@/utils/errors";
import { formatDateTime } from "@/utils/format";
import { PASSWORD_MIN, checkPassword, normalizeOtp } from "@/utils/password";
import { roleList } from "@/utils/roles";
import { can } from "@/utils/maintenance";
import { describeDevice } from "@/utils/device";

const keys = {
  me: ["account", "me"] as const,
  mfa: ["account", "mfa"] as const,
  sessions: ["account", "sessions"] as const,
  tenantSec: ["account", "tenant-security"] as const,
};

const SECTIONS = [
  { id: "perfil", label: "Perfil" },
  { id: "senha", label: "Senha" },
  { id: "mfa", label: "Verificação em duas etapas" },
  { id: "sessoes", label: "Sessões" },
  { id: "privacidade", label: "Privacidade e dados" },
] as const;

function Section({ id, title, description, children, action }: { id: string; title: string; description?: ReactNode; children: ReactNode; action?: ReactNode }) {
  return (
    <Card id={id} aria-labelledby={`${id}-h`} role="region" className="scroll-mt-4">
      <CardHeader id={`${id}-h`} title={title} description={description} action={action} />
      {children}
    </Card>
  );
}

/** "Minha conta": profile, password, MFA, sessions and the LGPD rights (export / delete). */
export default function AccountPage() {
  const me = useQuery({ queryKey: keys.me, queryFn: () => accountApi.me().then((r) => r.data) });
  const user = useAuthStore((s) => s.user);
  return (
    <div className="mx-auto grid max-w-5xl gap-6 lg:grid-cols-[12rem,1fr]">
      <nav aria-label="Seções da conta" className="lg:sticky lg:top-0 lg:self-start">
        <ul className="flex gap-1 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible">
          {SECTIONS.map((s) => (
            <li key={s.id} className="shrink-0">
              <a href={`#${s.id}`} className="block rounded-lg px-3 py-2 text-body text-fg-muted hover:bg-surface-2 hover:text-fg">{s.label}</a>
            </li>
          ))}
        </ul>
      </nav>
      <div className="min-w-0 space-y-6">
        <ProfileSection query={me} />
        <PasswordSection email={me.data?.email} name={me.data?.name} />
        <MfaSection />
        <SessionsSection />
        {can(user, "users.admin") && <TenantSecuritySection mfaEnabled={!!me.data?.mfa_enabled} />}
        <PrivacySection />
      </div>
    </div>
  );
}

// ── perfil ───────────────────────────────────────────────────────

function ProfileSection({ query }: { query: UseQueryResult<AccountMe> }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  useEffect(() => { if (query.data) setName(query.data.name); }, [query.data]);
  const save = useMutation({
    mutationFn: () => accountApi.updateProfile(name.trim()).then((r) => r.data),
    onSuccess: (d) => {
      qc.setQueryData(keys.me, d);
      const u = useAuthStore.getState().user;
      if (u) useAuthStore.setState({ user: { ...u, name: d.name } });
    },
  });
  return (
    <Section id="perfil" title="Perfil" description="Seus dados de identificação no FactoryConnect.">
      {query.isPending ? (
        <LoadingRegion label="Carregando perfil…"><Skeleton className="h-10" /><Skeleton className="mt-3 h-24" /></LoadingRegion>
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : (
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <dl className="grid gap-x-6 gap-y-2 text-body sm:grid-cols-2">
            <div><dt className="text-caption text-fg-muted">E-mail</dt><dd className="break-all text-fg">{query.data.email}</dd></div>
            <div><dt className="text-caption text-fg-muted">Papéis</dt><dd className="text-fg">{roleList(query.data) || "—"}</dd></div>
            <div><dt className="text-caption text-fg-muted">Conta criada em</dt><dd className="text-fg">{formatDateTime(query.data.created_at)}</dd></div>
            <div><dt className="text-caption text-fg-muted">Último acesso</dt><dd className="text-fg">{formatDateTime(query.data.last_login_at)}</dd></div>
          </dl>
          <Field label="Nome" htmlFor="acc-name">
            <input id="acc-name" required maxLength={255} autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} className={inputClass} />
          </Field>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" variant="primary" loading={save.isPending} disabled={!name.trim() || name.trim() === query.data.name}>Salvar nome</Button>
            {save.isSuccess && <p role="status" className="text-caption text-ok">Nome atualizado.</p>}
            {save.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(save.error, "Não foi possível salvar.")}</p>}
          </div>
        </form>
      )}
    </Section>
  );
}

// ── senha ────────────────────────────────────────────────────────

function PasswordSection({ email, name }: { email?: string; name?: string }) {
  const qc = useQueryClient();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [clientError, setClientError] = useState("");
  const change = useMutation({
    mutationFn: () => accountApi.changePassword(current, next),
    onSuccess: () => {
      setCurrent(""); setNext(""); setConfirm("");
      void qc.invalidateQueries({ queryKey: keys.sessions });
      void qc.invalidateQueries({ queryKey: keys.me });
    },
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    setClientError("");
    const c = checkPassword(next, [email, name]);
    if (!c.ok) { setClientError(c.problem ?? ""); return; }
    if (next !== confirm) { setClientError("As senhas não coincidem."); return; }
    change.mutate();
  }
  return (
    <Section id="senha" title="Senha" description="Ao trocar a senha, as outras sessões abertas são encerradas (esta continua).">
      <form className="grid gap-4 sm:grid-cols-2" onSubmit={submit}>
        <Field label="Senha atual" htmlFor="pw-current" className="sm:col-span-2">
          <PasswordInput id="pw-current" value={current} onChange={setCurrent} autoComplete="current-password" />
        </Field>
        <Field label="Nova senha" htmlFor="pw-new" hint={`Mínimo ${PASSWORD_MIN} caracteres; não pode repetir senhas recentes.`}>
          <PasswordInput id="pw-new" value={next} onChange={setNext} autoComplete="new-password" meter />
        </Field>
        <Field label="Confirme a nova senha" htmlFor="pw-confirm">
          <PasswordInput id="pw-confirm" value={confirm} onChange={setConfirm} autoComplete="new-password" />
        </Field>
        <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
          <Button type="submit" variant="primary" loading={change.isPending}>Trocar senha</Button>
          {change.isSuccess && <p role="status" className="text-caption text-ok">Senha alterada. As outras sessões foram encerradas.</p>}
          {(clientError || change.isError) && (
            <p role="alert" className="text-caption text-danger">{clientError || apiErrorMessage(change.error, "Não foi possível trocar a senha.")}</p>
          )}
        </div>
      </form>
    </Section>
  );
}

// ── MFA ──────────────────────────────────────────────────────────

function QrCode({ uri }: { uri: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    import("qrcode")
      .then((m) => m.toString(uri, { type: "svg", margin: 1, errorCorrectionLevel: "M", color: { dark: "#000000", light: "#ffffff" } }))
      .then((s) => { if (alive) setSvg(s); })
      .catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
  }, [uri]);
  if (failed) return <p className="text-caption text-fg-muted">Não foi possível desenhar o QR code: use a chave abaixo.</p>;
  if (!svg) return <Skeleton className="size-48" />;
  return (
    <div role="img" aria-label="QR code para cadastrar no aplicativo autenticador"
      className="size-48 rounded-lg bg-white p-2 [&>svg]:size-full" dangerouslySetInnerHTML={{ __html: svg }} />
  );
}

function RecoveryCodes({ codes }: { codes: string[] }) {
  const text = codes.join("\n");
  function download() {
    const blob = new Blob([`FactoryConnect — códigos de recuperação\nCada código vale uma vez.\n\n${text}\n`], { type: "text/plain" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "factoryconnect-codigos-de-recuperacao.txt";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }
  return (
    <div className="space-y-3" data-testid="recovery-codes">
      <AuthNotice tone="info">Guarde estes códigos em lugar seguro. Eles só aparecem agora; cada um vale uma única vez se você perder o celular.</AuthNotice>
      <ul className="grid grid-cols-2 gap-2 rounded-lg bg-surface-2 p-3 font-mono text-body text-fg">
        {codes.map((c) => <li key={c}>{c}</li>)}
      </ul>
      <div className="flex flex-wrap gap-2">
        <CopyButton text={text} label="Copiar códigos" />
        <Button size="sm" onClick={download}>Baixar .txt</Button>
      </div>
    </div>
  );
}

function MfaSection() {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: keys.mfa, queryFn: () => accountApi.mfa().then((r) => r.data) });
  const [mode, setMode] = useState<null | "enroll" | "disable" | "codes">(null);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [enroll, setEnroll] = useState<MfaEnrollment | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);

  const reset = () => { setMode(null); setPassword(""); setCode(""); setEnroll(null); start.reset(); confirm.reset(); disable.reset(); regen.reset(); };
  const refresh = () => { void qc.invalidateQueries({ queryKey: keys.mfa }); void qc.invalidateQueries({ queryKey: keys.me }); };

  const start = useMutation({ mutationFn: () => accountApi.mfaEnroll(password).then((r) => r.data), onSuccess: (d) => { setEnroll(d); setPassword(""); } });
  const confirm = useMutation({
    mutationFn: () => accountApi.mfaConfirm(normalizeOtp(code)).then((r) => r.data),
    onSuccess: (d) => { setCodes(d.recovery_codes); setMode(null); setEnroll(null); setCode(""); refresh(); },
  });
  const disable = useMutation({ mutationFn: () => accountApi.mfaDisable(password, normalizeOtp(code)), onSuccess: () => { reset(); setCodes(null); refresh(); } });
  const regen = useMutation({
    mutationFn: () => accountApi.mfaRecoveryCodes(normalizeOtp(code)).then((r) => r.data),
    onSuccess: (d) => { setCodes(d.recovery_codes); setMode(null); setCode(""); refresh(); },
  });

  const s = status.data;
  return (
    <Section id="mfa" title="Verificação em duas etapas (MFA)"
      description="Além da senha, um código de 6 dígitos do aplicativo autenticador (Google Authenticator, Microsoft Authenticator, 1Password…).">
      {status.isPending ? <LoadingRegion label="Carregando MFA…"><Skeleton className="h-16" /></LoadingRegion>
        : status.isError ? <ErrorState error={status.error} onRetry={() => void status.refetch()} />
        : s && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="flex items-center gap-2 text-body" data-testid="mfa-status">
              <span aria-hidden="true" className={clsx("size-2.5 rounded-full", s.enabled ? "bg-ok-solid" : "bg-warn-solid")} />
              {s.enabled
                ? <span className="text-fg">Ativa desde {formatDateTime(s.enabled_at)} · {s.recovery_codes_left} código(s) de recuperação restantes</span>
                : <span className="text-fg">Desativada{s.required ? " — obrigatória para administradores desta empresa" : ""}</span>}
            </p>
            {mode === null && (
              <div className="flex flex-wrap gap-2">
                {s.enabled ? (
                  <>
                    <Button size="sm" onClick={() => { setMode("codes"); setCodes(null); }}>Gerar novos códigos</Button>
                    {!s.required && <Button size="sm" variant="danger-ghost" onClick={() => { setMode("disable"); setCodes(null); }}>Desativar</Button>}
                  </>
                ) : (
                  <Button size="sm" variant="primary" onClick={() => { setMode("enroll"); setCodes(null); }}>Ativar verificação em duas etapas</Button>
                )}
              </div>
            )}
          </div>
          {s.enabled && s.recovery_codes_left <= 2 && !codes && (
            <AuthNotice tone="info">Restam poucos códigos de recuperação. Gere um novo conjunto.</AuthNotice>
          )}

          {mode === "enroll" && !enroll && (
            <form className="space-y-3 rounded-lg border border-line p-4" onSubmit={(e) => { e.preventDefault(); start.mutate(); }}>
              <p className="text-body text-fg">Passo 1 de 2 — confirme sua senha.</p>
              <Field label="Senha atual" htmlFor="mfa-pw"><PasswordInput id="mfa-pw" value={password} onChange={setPassword} autoComplete="current-password" /></Field>
              {start.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(start.error, "Não foi possível iniciar.")}</p>}
              <div className="flex gap-2"><Button type="submit" variant="primary" loading={start.isPending}>Continuar</Button><Button variant="ghost" onClick={reset}>Cancelar</Button></div>
            </form>
          )}
          {mode === "enroll" && enroll && (
            <form className="space-y-4 rounded-lg border border-line p-4" onSubmit={(e) => { e.preventDefault(); confirm.mutate(); }} data-testid="mfa-enroll">
              <p className="text-body text-fg">Passo 2 de 2 — escaneie o QR code no aplicativo autenticador e digite o código que aparecer.</p>
              <div className="flex flex-wrap items-start gap-4">
                <QrCode uri={enroll.otpauth_uri} />
                <div className="min-w-0 flex-1 space-y-2">
                  <p className="text-caption text-fg-muted">Sem câmera? Digite esta chave no aplicativo:</p>
                  <p className="break-all rounded-md bg-surface-2 px-2 py-1 font-mono text-body text-fg" data-testid="mfa-secret">{enroll.secret}</p>
                  <CopyButton text={enroll.secret} label="Copiar chave" />
                </div>
              </div>
              <Field label="Código de 6 dígitos" htmlFor="mfa-confirm-code">
                <input id="mfa-confirm-code" inputMode="numeric" autoComplete="one-time-code" required maxLength={7} value={code}
                  onChange={(e) => setCode(e.target.value)} className={clsx(inputClass, "max-w-[12rem] font-mono tracking-[0.25em]")} />
              </Field>
              {confirm.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(confirm.error, "Código inválido.")}</p>}
              <div className="flex gap-2"><Button type="submit" variant="primary" loading={confirm.isPending}>Ativar</Button><Button variant="ghost" onClick={reset}>Cancelar</Button></div>
            </form>
          )}
          {mode === "disable" && (
            <form className="space-y-3 rounded-lg border border-danger-solid/40 p-4" onSubmit={(e) => { e.preventDefault(); disable.mutate(); }}>
              <p className="text-body text-fg">Desativar deixa a conta protegida só pela senha.</p>
              <Field label="Senha atual" htmlFor="mfa-dis-pw"><PasswordInput id="mfa-dis-pw" value={password} onChange={setPassword} autoComplete="current-password" /></Field>
              <Field label="Código do autenticador ou de recuperação" htmlFor="mfa-dis-code">
                <input id="mfa-dis-code" autoComplete="one-time-code" required maxLength={32} value={code} onChange={(e) => setCode(e.target.value)} className={clsx(inputClass, "max-w-xs font-mono")} />
              </Field>
              {disable.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(disable.error, "Não foi possível desativar.")}</p>}
              <div className="flex gap-2"><Button type="submit" variant="danger" loading={disable.isPending}>Desativar MFA</Button><Button variant="ghost" onClick={reset}>Cancelar</Button></div>
            </form>
          )}
          {mode === "codes" && (
            <form className="space-y-3 rounded-lg border border-line p-4" onSubmit={(e) => { e.preventDefault(); regen.mutate(); }}>
              <p className="text-body text-fg">Os códigos antigos deixam de valer.</p>
              <Field label="Código atual do autenticador" htmlFor="mfa-regen-code">
                <input id="mfa-regen-code" inputMode="numeric" autoComplete="one-time-code" required maxLength={7} value={code} onChange={(e) => setCode(e.target.value)} className={clsx(inputClass, "max-w-[12rem] font-mono tracking-[0.25em]")} />
              </Field>
              {regen.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(regen.error, "Código inválido.")}</p>}
              <div className="flex gap-2"><Button type="submit" variant="primary" loading={regen.isPending}>Gerar códigos</Button><Button variant="ghost" onClick={reset}>Cancelar</Button></div>
            </form>
          )}
          {codes && <RecoveryCodes codes={codes} />}
        </div>
      )}
    </Section>
  );
}

// ── sessões ──────────────────────────────────────────────────────

function SessionRow({ s, onRevoke, busy }: { s: AccountSession; onRevoke: () => void; busy: boolean }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 py-3" data-testid="session-row">
      <div className="min-w-0">
        <p className="text-body font-medium text-fg">
          {describeDevice(s.user_agent)}
          {s.current && <span className="ml-2 rounded-full bg-ok-solid/15 px-2 py-0.5 text-caption font-medium text-ok">Esta sessão</span>}
        </p>
        <p className="text-caption text-fg-muted">
          Último uso {formatDateTime(s.last_used_at)} · início {formatDateTime(s.created_at)}
          {s.ip_prefix ? ` · rede ${s.ip_prefix}` : ""}{s.mfa ? " · com MFA" : ""}
        </p>
      </div>
      {!s.current && <Button size="sm" variant="danger-ghost" loading={busy} onClick={onRevoke}>Encerrar<span className="sr-only"> sessão {describeDevice(s.user_agent)}</span></Button>}
    </li>
  );
}

function SessionsSection() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: keys.sessions, queryFn: () => accountApi.sessions().then((r) => r.data), refetchInterval: 60_000 });
  const revoke = useMutation({ mutationFn: (id: string) => accountApi.revokeSession(id), onSuccess: () => void qc.invalidateQueries({ queryKey: keys.sessions }) });
  const revokeOthers = useMutation({ mutationFn: () => accountApi.revokeOtherSessions(), onSuccess: () => void qc.invalidateQueries({ queryKey: keys.sessions }) });
  const others = (list.data ?? []).filter((s) => !s.current).length;
  return (
    <Section id="sessoes" title="Sessões ativas" description="Dispositivos conectados à sua conta. Encerre os que você não reconhece e troque a senha."
      action={others > 0 ? <Button size="sm" variant="danger-ghost" loading={revokeOthers.isPending} onClick={() => revokeOthers.mutate()}>Encerrar as outras ({others})</Button> : undefined}>
      {list.isPending ? <LoadingRegion label="Carregando sessões…"><Skeleton className="h-14" /><Skeleton className="mt-2 h-14" /></LoadingRegion>
        : list.isError ? <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        : list.data.length === 0 ? <EmptyState title="Nenhuma sessão ativa" />
        : (
          <ul className="divide-y divide-line" aria-live="polite">
            {list.data.map((s) => <SessionRow key={s.id} s={s} busy={revoke.isPending && revoke.variables === s.id} onRevoke={() => revoke.mutate(s.id)} />)}
          </ul>
        )}
      {(revoke.isError || revokeOthers.isError) && (
        <p role="alert" className="mt-2 text-caption text-danger">{apiErrorMessage(revoke.error ?? revokeOthers.error, "Não foi possível encerrar a sessão.")}</p>
      )}
    </Section>
  );
}

// ── política do tenant (admin) ───────────────────────────────────

function TenantSecuritySection({ mfaEnabled }: { mfaEnabled: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: keys.tenantSec, queryFn: () => accountApi.tenantSecurity().then((r) => r.data) });
  const save = useMutation({
    mutationFn: (v: boolean) => accountApi.setTenantSecurity(v).then((r) => r.data),
    onSuccess: (d) => { qc.setQueryData(keys.tenantSec, d); void qc.invalidateQueries({ queryKey: keys.mfa }); },
  });
  return (
    <Section id="politica" title="Política de segurança da empresa" description="Vale para todos os administradores do seu tenant.">
      {q.isPending ? <Skeleton className="h-10" /> : q.isError ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : (
        <div className="space-y-2">
          <label className="flex items-start gap-3 text-body text-fg">
            <input type="checkbox" className="mt-1 size-4 accent-[rgb(var(--c-accent))]" checked={q.data.require_mfa_admins}
              disabled={save.isPending || (!mfaEnabled && !q.data.require_mfa_admins)}
              onChange={(e) => save.mutate(e.target.checked)} />
            <span>Exigir verificação em duas etapas de todos os administradores
              {!mfaEnabled && !q.data.require_mfa_admins && <span className="block text-caption text-fg-muted">Ative o MFA na sua conta antes de exigi-lo dos outros.</span>}
            </span>
          </label>
          {save.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(save.error, "Não foi possível salvar.")}</p>}
        </div>
      )}
    </Section>
  );
}

// ── privacidade (LGPD) ───────────────────────────────────────────

function PrivacySection() {
  const navigate = useNavigate();
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [typed, setTyped] = useState("");
  const del = useMutation({
    mutationFn: () => accountApi.deleteAccount(password),
    onSuccess: () => {
      // the account no longer exists: drop the local session without calling /logout
      localStorage.removeItem("access_token");
      localStorage.removeItem("refresh_token");
      useAuthStore.getState().logout();
      navigate("/login?deleted=1", { replace: true });
    },
  });

  async function exportData() {
    setExportError("");
    setExporting(true);
    try {
      const r = await accountApi.exportData();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(r.data);
      a.download = `factoryconnect-meus-dados-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    } catch (err) {
      setExportError(apiErrorMessage(err, "Não foi possível exportar agora."));
    } finally {
      setExporting(false);
    }
  }

  return (
    <Section id="privacidade" title="Privacidade e seus dados (LGPD)"
      description={<>Seus direitos de titular (art. 18 da LGPD). Detalhes na <a className="font-medium text-ok underline-offset-2 hover:underline" href="https://factoryconnect.tech/privacidade" target="_blank" rel="noreferrer">política de privacidade</a>.</>}>
      <div className="space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-body font-medium text-fg">Exportar meus dados</p>
            <p className="text-caption text-fg-muted">Um arquivo JSON com todos os dados pessoais que o sistema guarda sobre você.</p>
          </div>
          <Button onClick={() => void exportData()} loading={exporting} demoSafe>Baixar meus dados</Button>
        </div>
        {exportError && <p role="alert" className="text-caption text-danger">{exportError}</p>}
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-5">
          <div className="min-w-0">
            <p className="text-body font-medium text-fg">Excluir minha conta</p>
            <p className="text-caption text-fg-muted">Seu acesso é encerrado e seus dados pessoais são anonimizados. O histórico de produção e manutenção continua, sem o seu nome.</p>
          </div>
          <Button variant="danger-ghost" onClick={() => setOpen(true)}>Excluir conta…</Button>
        </div>
      </div>
      <Sheet open={open} onClose={() => { setOpen(false); setPassword(""); setTyped(""); del.reset(); }} title="Excluir minha conta"
        description="Esta ação não pode ser desfeita."
        footer={
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancelar</Button>
            <Button variant="danger" form="delete-account" type="submit" loading={del.isPending} disabled={typed !== "EXCLUIR" || !password}>Excluir definitivamente</Button>
          </div>
        }>
        <form id="delete-account" className="space-y-4" onSubmit={(e) => { e.preventDefault(); if (typed === "EXCLUIR") del.mutate(); }}>
          <ul className="list-disc space-y-1 pl-5 text-body text-fg-muted">
            <li>Todas as suas sessões são encerradas e você não consegue mais entrar.</li>
            <li>Nome e e-mail são substituídos por um pseudônimo nos registros.</li>
            <li>Se você é o único administrador, promova outra pessoa antes.</li>
          </ul>
          <Field label="Senha atual" htmlFor="del-pw"><PasswordInput id="del-pw" value={password} onChange={setPassword} autoComplete="current-password" /></Field>
          <Field label={<>Digite <strong className="font-mono text-fg">EXCLUIR</strong> para confirmar</>} htmlFor="del-confirm">
            <input id="del-confirm" autoComplete="off" value={typed} onChange={(e) => setTyped(e.target.value)} className={clsx(inputClass, "font-mono")} />
          </Field>
          {del.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(del.error, "Não foi possível excluir a conta.")}</p>}
        </form>
      </Sheet>
    </Section>
  );
}
