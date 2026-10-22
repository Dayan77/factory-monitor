import { useEffect, useId, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import clsx from "clsx";
import ThemeToggle from "@/components/ThemeToggle";
import { inputClass } from "@/components/ui/Field";
import { passwordStrength } from "@/utils/password";

/** Frame of the public auth screens (login, MFA, reset, invitation): themed, one centered card, skip-free. */
export default function AuthShell({ title, subtitle, children, footer }: {
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  useEffect(() => { document.title = `${title} · FactoryConnect`; }, [title]);
  return (
    <div className="flex min-h-[100dvh] flex-col bg-canvas text-fg">
      <div className="flex justify-end p-3"><ThemeToggle /></div>
      <main className="flex flex-1 items-start justify-center px-4 pb-12 pt-2 sm:items-center">
        <div className="w-full max-w-sm space-y-6">
          <div className="flex flex-col items-center gap-3 text-center">
            <span className="flex size-14 items-center justify-center rounded-2xl bg-brand-600" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="currentColor" className="size-8 text-white"><path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" /></svg>
            </span>
            <div>
              <p className="text-caption font-semibold uppercase tracking-wide text-fg-subtle">FactoryConnect</p>
              <h1 className="text-title text-fg">{title}</h1>
              {subtitle && <p className="mt-1 text-body text-fg-muted">{subtitle}</p>}
            </div>
          </div>
          <div className="surface rounded-2xl border border-line bg-surface p-5 shadow-card sm:p-6">{children}</div>
          {footer && <div className="space-y-2 text-center text-body text-fg-muted">{footer}</div>}
          <p className="text-center">
            <Link to="/" className="text-caption text-fg-muted underline-offset-2 hover:text-fg hover:underline">← Voltar ao site</Link>
          </p>
        </div>
      </main>
    </div>
  );
}

/** Notice inside an auth card (info / success / error). Errors are announced (role=alert). */
export function AuthNotice({ tone, children, testId }: { tone: "info" | "ok" | "error"; children: ReactNode; testId?: string }) {
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      data-testid={testId}
      className={clsx(
        "rounded-lg border px-3 py-2 text-body",
        tone === "error" && "border-danger-solid/40 bg-danger-solid/10 text-danger",
        tone === "ok" && "border-ok-solid/40 bg-ok-solid/10 text-ok",
        tone === "info" && "border-info-solid/40 bg-info-solid/10 text-info",
      )}
    >
      {children}
    </div>
  );
}

const STRENGTH = ["Muito fraca", "Fraca", "Razoável", "Boa", "Forte"] as const;

/** Password input with show/hide toggle and, for new passwords, a strength bar. */
export function PasswordInput({ id, value, onChange, autoComplete, meter = false, required = true, describedBy, invalid }: {
  id: string;
  value: string;
  onChange: (v: string) => void;
  autoComplete: "current-password" | "new-password";
  meter?: boolean;
  required?: boolean;
  describedBy?: string;
  invalid?: boolean;
}) {
  const [show, setShow] = useState(false);
  const meterId = useId();
  const s = passwordStrength(value);
  return (
    <div>
      <div className="relative">
        <input
          id={id}
          type={show ? "text" : "password"}
          autoComplete={autoComplete}
          required={required}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          aria-describedby={[describedBy, meter && value ? meterId : null].filter(Boolean).join(" ") || undefined}
          aria-invalid={invalid || undefined}
          className={clsx(inputClass, "pr-20")}
        />
        <button
          type="button"
          onClick={() => setShow((v) => !v)}
          aria-pressed={show}
          aria-controls={id}
          className="absolute inset-y-0 right-1 my-1 rounded-md px-2 text-caption font-medium text-fg-muted hover:bg-surface-2 hover:text-fg"
        >
          {show ? "Ocultar" : "Mostrar"}
          <span className="sr-only"> senha</span>
        </button>
      </div>
      {meter && value && (
        <div className="mt-1.5 flex items-center gap-2" id={meterId}>
          <div className="flex flex-1 gap-1" aria-hidden="true">
            {[0, 1, 2, 3].map((i) => (
              <span key={i} className={clsx("h-1 flex-1 rounded-full",
                i < s ? (s <= 1 ? "bg-danger-solid" : s === 2 ? "bg-warn-solid" : "bg-ok-solid") : "bg-line-strong")} />
            ))}
          </div>
          <span className="text-caption text-fg-muted">Força: {STRENGTH[s]}</span>
        </div>
      )}
    </div>
  );
}
