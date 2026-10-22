import { PASSWORD_MIN } from "@/utils/password";
import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { authApi } from "@/services/api";
import { apiErrorMessage } from "@/utils/errors";

export default function RegisterPage() {
  const navigate = useNavigate();
  const [form, setForm] = useState({
    company_name: "",
    admin_name: "",
    admin_email: "",
    admin_password: "",
    confirm_password: "",
  });
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState("");

  function field(key: keyof typeof form) {
    return {
      value: form[key],
      onChange: (e: React.ChangeEvent<HTMLInputElement>) =>
        setForm((f) => ({ ...f, [key]: e.target.value })),
    };
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");

    if (form.admin_password !== form.confirm_password) {
      setError("As senhas não coincidem.");
      return;
    }
    if (form.admin_password.length < PASSWORD_MIN) {
      setError("A senha deve ter no mínimo 12 caracteres.");
      return;
    }

    setLoading(true);
    try {
      await authApi.register({
        company_name:   form.company_name,
        admin_name:     form.admin_name,
        admin_email:    form.admin_email,
        admin_password: form.admin_password,
      });
      navigate("/login?registered=1", { replace: true });
    } catch (err: unknown) {
      setError(apiErrorMessage(err, "Erro ao criar conta. Tente novamente."));
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen bg-gray-950">
      {/* Left panel */}
      <div className="hidden lg:flex w-1/2 flex-col justify-between bg-gray-900 border-r border-gray-800 p-12">
        <Link to="/" className="flex items-center gap-2.5">
          <span className="flex size-9 items-center justify-center rounded-xl bg-brand-500">
            <svg viewBox="0 0 24 24" fill="currentColor" className="size-5 text-white">
              <path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" />
            </svg>
          </span>
          <span className="text-base font-bold text-gray-50">FactoryConnect</span>
        </Link>

        <div className="space-y-8">
          <div>
            <h2 className="text-3xl font-bold text-gray-50">Comece a monitorar<br />sua fábrica hoje.</h2>
            <p className="mt-4 text-gray-400">14 dias grátis, sem cartão de crédito. Configure em minutos.</p>
          </div>
          <div className="space-y-4">
            {[
              "Conecte PLCs via OPC UA e Modbus TCP",
              "Dashboards de OEE em tempo real",
              "Alertas inteligentes por e-mail e WhatsApp",
              "IA para análise e insights da produção",
            ].map((item) => (
              <div key={item} className="flex items-center gap-3 text-sm text-gray-300">
                <span className="flex size-5 items-center justify-center rounded-full bg-brand-500/20 text-brand-400 shrink-0">
                  <svg viewBox="0 0 20 20" fill="currentColor" className="size-3">
                    <path fillRule="evenodd" d="M16.704 4.153a.75.75 0 0 1 .143 1.052l-8 10.5a.75.75 0 0 1-1.127.075l-4.5-4.5a.75.75 0 0 1 1.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 0 1 1.05-.143z" clipRule="evenodd" />
                  </svg>
                </span>
                {item}
              </div>
            ))}
          </div>
        </div>

        <p className="text-xs text-fg-muted">© 2026 FactoryConnect. Todos os direitos reservados.</p>
      </div>

      {/* Right panel — form */}
      <div className="flex flex-1 items-center justify-center p-6">
        <div className="w-full max-w-md space-y-7">
          {/* Mobile logo */}
          <div className="flex lg:hidden justify-center">
            <Link to="/" className="flex items-center gap-2">
              <span className="flex size-9 items-center justify-center rounded-xl bg-brand-500">
                <svg viewBox="0 0 24 24" fill="currentColor" className="size-5 text-white">
                  <path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" />
                </svg>
              </span>
              <span className="text-base font-bold text-gray-50">FactoryConnect</span>
            </Link>
          </div>

          <div>
            <h1 className="text-2xl font-bold text-gray-50">Criar conta gratuita</h1>
            <p className="mt-1 text-sm text-fg-muted">14 dias de trial — sem compromisso.</p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1.5">Nome da empresa</label>
              <input required placeholder="Acme Indústria Ltda"
                className="w-full rounded-xl border border-gray-700 bg-gray-800 px-4 py-3 text-sm text-gray-100 placeholder-gray-500 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
                {...field("company_name")} />
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1.5">Seu nome</label>
              <input required placeholder="João Silva"
                className="w-full rounded-xl border border-gray-700 bg-gray-800 px-4 py-3 text-sm text-gray-100 placeholder-gray-500 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
                {...field("admin_name")} />
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1.5">E-mail</label>
              <input required type="email" placeholder="joao@empresa.com"
                className="w-full rounded-xl border border-gray-700 bg-gray-800 px-4 py-3 text-sm text-gray-100 placeholder-gray-500 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
                {...field("admin_email")} />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Senha</label>
                <input required type="password" placeholder="Mín. 12 caracteres"
                  className="w-full rounded-xl border border-gray-700 bg-gray-800 px-4 py-3 text-sm text-gray-100 placeholder-gray-500 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
                  {...field("admin_password")} />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Confirmar senha</label>
                <input required type="password" placeholder="Repita a senha"
                  className="w-full rounded-xl border border-gray-700 bg-gray-800 px-4 py-3 text-sm text-gray-100 placeholder-gray-500 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
                  {...field("confirm_password")} />
              </div>
            </div>

            {error && (
              <p className="rounded-lg bg-red-500/10 border border-red-500/20 px-3 py-2 text-xs text-red-400">
                {error}
              </p>
            )}

            <button type="submit" disabled={loading}
              className="w-full rounded-xl bg-accent py-3 text-sm font-semibold text-accent-fg hover:bg-accent-hover disabled:opacity-50 transition-colors">
              {loading ? "Criando conta..." : "Criar conta grátis"}
            </button>
          </form>

          <p className="text-center text-sm text-fg-muted">
            Já tem conta?{" "}
            <Link to="/login" className="text-brand-400 hover:text-brand-300 font-medium">
              Fazer login
            </Link>
          </p>
        </div>
      </div>
    </main>
  );
}
