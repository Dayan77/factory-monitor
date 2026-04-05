import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

// ── Ícones inline ─────────────────────────────────────────────────

function Icon({ d, size = 6 }: { d: string; size?: number }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
      className={`size-${size}`}>
      <path strokeLinecap="round" strokeLinejoin="round" d={d} />
    </svg>
  );
}

// ── Navbar ────────────────────────────────────────────────────────

function Navbar() {
  const [scrolled, setScrolled] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 20);
    window.addEventListener("scroll", onScroll);
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  function scrollTo(id: string) {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
    setMenuOpen(false);
  }

  const links = [
    { label: "Início",      id: "hero" },
    { label: "Ferramentas", id: "features" },
    { label: "Cases",       id: "cases" },
    { label: "Quem Somos",  id: "about" },
    { label: "Contato",     id: "contact" },
  ];

  return (
    <header className={`fixed top-0 inset-x-0 z-50 transition-all duration-300 ${
      scrolled ? "bg-gray-950/95 backdrop-blur border-b border-gray-800 shadow-lg" : "bg-transparent"
    }`}>
      <nav className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
        {/* Logo */}
        <button onClick={() => scrollTo("hero")} className="flex items-center gap-2.5">
          <span className="flex size-9 items-center justify-center rounded-xl bg-brand-500">
            <svg viewBox="0 0 24 24" fill="currentColor" className="size-5 text-white">
              <path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" />
            </svg>
          </span>
          <span className="text-base font-bold text-white">FactoryConnect</span>
        </button>

        {/* Desktop links */}
        <div className="hidden md:flex items-center gap-6">
          {links.map((l) => (
            <button key={l.id} onClick={() => scrollTo(l.id)}
              className="text-sm text-gray-400 hover:text-white transition-colors">
              {l.label}
            </button>
          ))}
        </div>

        {/* CTAs */}
        <div className="hidden md:flex items-center gap-3">
          <Link to="/login"
            className="rounded-lg px-4 py-2 text-sm font-medium text-gray-300 hover:text-white hover:bg-gray-800 transition-colors">
            Login
          </Link>
          <Link to="/register"
            className="rounded-lg bg-brand-500 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-600 transition-colors">
            Comece Grátis
          </Link>
        </div>

        {/* Mobile menu toggle */}
        <button className="md:hidden text-gray-400 hover:text-white" onClick={() => setMenuOpen(!menuOpen)}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="size-6">
            {menuOpen
              ? <path strokeLinecap="round" d="M6 18L18 6M6 6l12 12" />
              : <path strokeLinecap="round" d="M4 6h16M4 12h16M4 18h16" />}
          </svg>
        </button>
      </nav>

      {/* Mobile menu */}
      {menuOpen && (
        <div className="md:hidden border-t border-gray-800 bg-gray-950 px-6 py-4 space-y-3">
          {links.map((l) => (
            <button key={l.id} onClick={() => scrollTo(l.id)}
              className="block w-full text-left text-sm text-gray-400 hover:text-white py-2">
              {l.label}
            </button>
          ))}
          <div className="flex gap-3 pt-2 border-t border-gray-800">
            <Link to="/login" className="flex-1 rounded-lg border border-gray-700 py-2 text-center text-sm text-gray-300 hover:bg-gray-800">Login</Link>
            <Link to="/register" className="flex-1 rounded-lg bg-brand-500 py-2 text-center text-sm font-semibold text-white hover:bg-brand-600">Comece Grátis</Link>
          </div>
        </div>
      )}
    </header>
  );
}

// ── Hero ──────────────────────────────────────────────────────────

function Hero() {
  return (
    <section id="hero" className="relative min-h-screen flex items-center bg-gray-950 overflow-hidden">
      {/* Background grid */}
      <div className="absolute inset-0 bg-[linear-gradient(rgba(37,163,104,0.03)_1px,transparent_1px),linear-gradient(90deg,rgba(37,163,104,0.03)_1px,transparent_1px)] bg-[size:60px_60px]" />
      {/* Glow */}
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-brand-500/5 rounded-full blur-3xl pointer-events-none" />

      <div className="relative mx-auto max-w-7xl px-6 py-32 text-center">
        <span className="inline-flex items-center gap-2 rounded-full border border-brand-500/30 bg-brand-500/10 px-4 py-1.5 text-xs font-medium text-brand-400 mb-8">
          <span className="size-1.5 rounded-full bg-brand-400 animate-pulse" />
          Indústria 4.0 — Monitoramento em tempo real
        </span>

        <h1 className="text-4xl sm:text-6xl font-extrabold text-white leading-tight mb-6">
          Conecte sua fábrica.<br />
          <span className="text-brand-400">Decida com dados.</span>
        </h1>

        <p className="mx-auto max-w-2xl text-lg text-gray-400 mb-10">
          Plataforma completa para monitoramento industrial via OPC UA e Modbus TCP,
          dashboards de OEE em tempo real, alertas inteligentes e análise com IA —
          tudo em um único sistema multi-empresa.
        </p>

        <div className="flex flex-wrap justify-center gap-4">
          <Link to="/register"
            className="rounded-xl bg-brand-500 px-8 py-3.5 text-base font-semibold text-white hover:bg-brand-600 transition-colors shadow-lg shadow-brand-500/20">
            Comece gratuitamente
          </Link>
          <button onClick={() => document.getElementById("features")?.scrollIntoView({ behavior: "smooth" })}
            className="rounded-xl border border-gray-700 bg-gray-900 px-8 py-3.5 text-base font-medium text-gray-300 hover:bg-gray-800 hover:border-gray-600 transition-colors">
            Ver funcionalidades
          </button>
        </div>

        {/* Dashboard mockup */}
        <div className="mt-20 mx-auto max-w-5xl">
          <div className="rounded-2xl border border-gray-800 bg-gray-900/80 backdrop-blur shadow-2xl overflow-hidden">
            {/* Titlebar */}
            <div className="flex items-center gap-2 border-b border-gray-800 px-5 py-3">
              <span className="size-3 rounded-full bg-red-500/60" />
              <span className="size-3 rounded-full bg-yellow-500/60" />
              <span className="size-3 rounded-full bg-green-500/60" />
              <span className="ml-3 text-xs text-gray-600 font-mono">factoryconnect.tech/dashboard</span>
            </div>
            {/* Fake dashboard */}
            <div className="p-6 grid grid-cols-4 gap-4">
              {[
                { label: "OEE Médio", value: "87.3%", color: "text-brand-400" },
                { label: "Máquinas Ativas", value: "12/14", color: "text-blue-400" },
                { label: "Alertas Ativos", value: "2", color: "text-yellow-400" },
                { label: "Uptime", value: "99.8%", color: "text-green-400" },
              ].map((s) => (
                <div key={s.label} className="rounded-xl border border-gray-800 bg-gray-950 p-4">
                  <p className="text-xs text-gray-500 mb-1">{s.label}</p>
                  <p className={`text-2xl font-bold font-mono ${s.color}`}>{s.value}</p>
                </div>
              ))}
            </div>
            <div className="px-6 pb-6 grid grid-cols-3 gap-4">
              {/* Fake chart bars */}
              <div className="col-span-2 rounded-xl border border-gray-800 bg-gray-950 p-4 h-32 flex items-end gap-1.5">
                {[60,75,55,80,70,87,65,90,72,85,78,87].map((h, i) => (
                  <div key={i} className="flex-1 rounded-sm bg-brand-500/40 hover:bg-brand-500/60 transition-colors"
                    style={{ height: `${h}%` }} />
                ))}
              </div>
              {/* Fake gauge */}
              <div className="rounded-xl border border-gray-800 bg-gray-950 p-4 flex flex-col items-center justify-center gap-2">
                <div className="relative size-16">
                  <svg viewBox="0 0 36 36" className="size-16 -rotate-90">
                    <circle cx="18" cy="18" r="14" fill="none" stroke="#1f2937" strokeWidth="4" />
                    <circle cx="18" cy="18" r="14" fill="none" stroke="#25a368" strokeWidth="4"
                      strokeDasharray="62 88" strokeLinecap="round" />
                  </svg>
                  <span className="absolute inset-0 flex items-center justify-center text-xs font-bold text-brand-400">87%</span>
                </div>
                <p className="text-xs text-gray-500">OEE Linha A</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

// ── Features ──────────────────────────────────────────────────────

const FEATURES = [
  {
    icon: "M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2v-4M9 21H5a2 2 0 0 1-2-2v-4m0 0h18",
    title: "Conectividade Industrial",
    desc: "Integração nativa com OPC UA e Modbus TCP. Conecte PLCs, inversores e sensores de qualquer fabricante sem código adicional.",
    tags: ["OPC UA", "Modbus TCP", "MQTT"],
  },
  {
    icon: "M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125z",
    title: "Dashboard OEE em Tempo Real",
    desc: "Visualize disponibilidade, desempenho e qualidade de cada linha de produção com atualização contínua via WebSocket.",
    tags: ["Tempo Real", "WebSocket", "OEE"],
  },
  {
    icon: "M14.857 17.082a23.848 23.848 0 0 0 5.454-1.31A8.967 8.967 0 0 1 18 9.75V9A6 6 0 0 0 6 9v.75a8.967 8.967 0 0 1-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 0 1-5.714 0m5.714 0a3 3 0 1 1-5.714 0",
    title: "Alertas Inteligentes",
    desc: "Regras configuráveis com cooldown automático. Notificações por e-mail, Telegram ou WhatsApp quando algo sair do limite.",
    tags: ["E-mail", "Telegram", "WhatsApp"],
  },
  {
    icon: "M7.5 14.25v2.25m3-4.5v4.5m3-6.75v6.75m3-9v9M6 20.25h12A2.25 2.25 0 0 0 20.25 18V6A2.25 2.25 0 0 0 18 3.75H6A2.25 2.25 0 0 0 3.75 6v12A2.25 2.25 0 0 0 6 20.25z",
    title: "IA & Análise Preditiva",
    desc: "Chat com IA treinado nos dados da sua fábrica. Identifique padrões, antecipe falhas e receba insights acionáveis automaticamente.",
    tags: ["OpenAI", "Insights", "Chat"],
  },
  {
    icon: "M12 21a9.004 9.004 0 0 0 8.716-6.747M12 21a9.004 9.004 0 0 1-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S9.515 3 12 3m0 0a8.997 8.997 0 0 1 7.843 4.582M12 3a8.997 8.997 0 0 0-7.843 4.582m15.686 0A11.953 11.953 0 0 1 12 10.5c-2.998 0-5.74-1.1-7.843-2.918m15.686 0A8.959 8.959 0 0 1 21 12c0 .778-.099 1.533-.284 2.253m0 0A17.919 17.919 0 0 1 12 16.5c-3.162 0-6.133-.815-8.716-2.247m0 0A9.015 9.015 0 0 1 3 12c0-1.605.42-3.113 1.157-4.418",
    title: "Multi-Empresa (SaaS)",
    desc: "Cada empresa tem seus dados 100% isolados. Controle de planos, limites de usuários e máquinas, trial e suspensão de contas.",
    tags: ["Multi-tenant", "RBAC", "SaaS"],
  },
  {
    icon: "M5.25 14.25h13.5m-13.5 0a3 3 0 0 1-3-3m3 3a3 3 0 1 0 6 0m-6 0H3m16.5 0a3 3 0 0 0 3-3m-3 3a3 3 0 1 1-6 0m6 0h1.5m-7.5 0v-8.25m0 0A3 3 0 0 0 9 3m3 3a3 3 0 0 1 3-3m-3 3v8.25m0-8.25A3 3 0 0 1 15 3m-3 0h.008v.008H12V3zm-3 3a3 3 0 0 0-3 3m3-3h.008v.008H9V6zm3 12a3 3 0 0 1-3-3m3 3h.008v.008H12v-.008zm3-12a3 3 0 0 1 3 3m-3-3h.008v.008H15V6z",
    title: "Edge Computing Seguro",
    desc: "Gateway local com buffer SQLite offline, filtro de deadband e criptografia AES-256. Os dados chegam ao servidor mesmo sem internet estável.",
    tags: ["AES-256", "Offline Buffer", "Edge"],
  },
];

function Features() {
  return (
    <section id="features" className="bg-gray-950 py-24">
      <div className="mx-auto max-w-7xl px-6">
        <div className="text-center mb-16">
          <span className="text-xs font-semibold uppercase tracking-widest text-brand-500">Ferramentas</span>
          <h2 className="mt-3 text-3xl sm:text-4xl font-bold text-white">Tudo que sua fábrica precisa</h2>
          <p className="mt-4 text-gray-400 max-w-2xl mx-auto">
            Da coleta de dados no chão de fábrica ao insight estratégico, o FactoryConnect cobre
            toda a cadeia de valor da sua operação industrial.
          </p>
        </div>

        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-6">
          {FEATURES.map((f) => (
            <div key={f.title}
              className="group rounded-2xl border border-gray-800 bg-gray-900 p-6 hover:border-brand-500/40 hover:bg-gray-900/80 transition-all duration-300">
              <div className="mb-4 inline-flex size-12 items-center justify-center rounded-xl bg-brand-500/10 text-brand-400 group-hover:bg-brand-500/15 transition-colors">
                <Icon d={f.icon} size={6} />
              </div>
              <h3 className="mb-2 text-base font-semibold text-white">{f.title}</h3>
              <p className="text-sm text-gray-400 leading-relaxed mb-4">{f.desc}</p>
              <div className="flex flex-wrap gap-2">
                {f.tags.map((t) => (
                  <span key={t} className="rounded-full bg-gray-800 px-2.5 py-0.5 text-xs text-gray-400">{t}</span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ── Cases ─────────────────────────────────────────────────────────

const CASES = [
  {
    company: "Metalúrgica Fonseca",
    segment: "Metalurgia",
    result: "+23% de OEE",
    desc: "Integração com 3 linhas de produção via Modbus TCP, com alertas de temperatura e pressão em tempo real. Redução de 40% nas paradas não planejadas.",
    metric: "23",
    unit: "%",
    label: "ganho de OEE",
  },
  {
    company: "Plásticos Norte",
    segment: "Injeção Plástica",
    result: "40% menos paradas",
    desc: "Monitoramento via OPC UA de 8 injetoras com alertas preditivos. O sistema identificou padrões de falha antes da ocorrência, eliminando perdas de turno.",
    metric: "40",
    unit: "%",
    label: "redução de paradas",
  },
  {
    company: "Alimentos BomDia",
    segment: "Alimentos & Bebidas",
    result: "ROI em 3 meses",
    desc: "Implantação em linha de envase com integração ao ERP. Dashboards em tempo real para supervisores e gerentes em qualquer dispositivo.",
    metric: "3",
    unit: "x",
    label: "ROI no primeiro ano",
  },
];

function Cases() {
  return (
    <section id="cases" className="bg-gray-900/50 py-24 border-y border-gray-800">
      <div className="mx-auto max-w-7xl px-6">
        <div className="text-center mb-16">
          <span className="text-xs font-semibold uppercase tracking-widest text-brand-500">Cases</span>
          <h2 className="mt-3 text-3xl sm:text-4xl font-bold text-white">Resultados reais</h2>
          <p className="mt-4 text-gray-400 max-w-xl mx-auto">
            Empresas de diferentes segmentos já utilizam o FactoryConnect para transformar
            dados industriais em decisões mais rápidas.
          </p>
        </div>

        <div className="grid md:grid-cols-3 gap-6">
          {CASES.map((c) => (
            <div key={c.company} className="rounded-2xl border border-gray-800 bg-gray-900 p-6 flex flex-col">
              <div className="flex items-center justify-between mb-4">
                <div>
                  <p className="font-semibold text-gray-100">{c.company}</p>
                  <p className="text-xs text-gray-500">{c.segment}</p>
                </div>
                <span className="rounded-full bg-brand-500/10 border border-brand-500/20 px-3 py-1 text-xs font-medium text-brand-400">
                  {c.result}
                </span>
              </div>
              <p className="text-sm text-gray-400 leading-relaxed flex-1">{c.desc}</p>
              <div className="mt-6 pt-4 border-t border-gray-800 flex items-baseline gap-1">
                <span className="text-4xl font-extrabold text-brand-400 font-mono">{c.metric}</span>
                <span className="text-xl font-bold text-brand-500">{c.unit}</span>
                <span className="ml-1 text-sm text-gray-500">{c.label}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ── About ─────────────────────────────────────────────────────────

function About() {
  return (
    <section id="about" className="bg-gray-950 py-24">
      <div className="mx-auto max-w-7xl px-6">
        <div className="grid lg:grid-cols-2 gap-16 items-center">
          <div>
            <span className="text-xs font-semibold uppercase tracking-widest text-brand-500">Quem Somos</span>
            <h2 className="mt-3 text-3xl sm:text-4xl font-bold text-white">
              Tecnologia industrial<br />feita por quem entende de fábrica
            </h2>
            <p className="mt-5 text-gray-400 leading-relaxed">
              O FactoryConnect nasceu da necessidade real de conectar o chão de fábrica ao mundo
              digital sem burocracia, sem integradores caros e sem meses de implantação.
            </p>
            <p className="mt-4 text-gray-400 leading-relaxed">
              Somos especialistas em automação industrial e desenvolvimento de software,
              com experiência em dezenas de plantas industriais nos setores de metalurgia,
              alimentos, têxtil e embalagens.
            </p>
            <div className="mt-8 grid grid-cols-2 gap-6">
              {[
                { value: "50+", label: "Plantas conectadas" },
                { value: "99.9%", label: "Uptime garantido" },
                { value: "< 1s", label: "Latência de dados" },
                { value: "24/7", label: "Suporte técnico" },
              ].map((s) => (
                <div key={s.label}>
                  <p className="text-3xl font-extrabold text-brand-400 font-mono">{s.value}</p>
                  <p className="text-sm text-gray-500 mt-0.5">{s.label}</p>
                </div>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            {[
              { icon: "M9 12.75 11.25 15 15 9.75m-3-7.036A11.959 11.959 0 0 1 3.598 6 11.99 11.99 0 0 0 3 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z", title: "Segurança", desc: "Dados criptografados em trânsito e em repouso. LGPD compliant." },
              { icon: "M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z", title: "Alta Performance", desc: "Suporte a milhares de tags por segundo com baixíssima latência." },
              { icon: "M11.42 15.17 17.25 21A2.652 2.652 0 0 0 21 17.25l-5.877-5.877M11.42 15.17l2.496-3.03c.317-.384.74-.626 1.208-.766M11.42 15.17l-4.655 5.653a2.548 2.548 0 1 1-3.586-3.586l6.837-5.63m5.108-.233c.55-.164 1.163-.188 1.743-.14a4.5 4.5 0 0 0 4.486-6.336l-3.276 3.277a3.004 3.004 0 0 1-2.25-2.25l3.276-3.276a4.5 4.5 0 0 0-6.336 4.486c.091 1.076-.071 2.264-.904 2.95l-.102.085m-1.745 1.437L5.909 7.5H4.5L2.25 3.75l1.5-1.5L7.5 4.5v1.409l4.26 4.26m-1.745 1.437 1.745-1.437m6.615 8.206L15.75 15.75M4.867 19.125h.008v.008h-.008v-.008z", title: "Implantação Rápida", desc: "Do zero à produção em menos de uma semana com suporte dedicado." },
              { icon: "M20.25 6.375c0 2.278-3.694 4.125-8.25 4.125S3.75 8.653 3.75 6.375m16.5 0c0-2.278-3.694-4.125-8.25-4.125S3.75 4.097 3.75 6.375m16.5 0v11.25c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125V6.375m16.5 2.25c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125", title: "Dados Isolados", desc: "Arquitetura multi-tenant — cada empresa vê apenas seus próprios dados." },
            ].map((v) => (
              <div key={v.title} className="rounded-2xl border border-gray-800 bg-gray-900 p-5">
                <div className="mb-3 inline-flex size-10 items-center justify-center rounded-xl bg-brand-500/10 text-brand-400">
                  <Icon d={v.icon} size={5} />
                </div>
                <h4 className="text-sm font-semibold text-white mb-1">{v.title}</h4>
                <p className="text-xs text-gray-500 leading-relaxed">{v.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

// ── Contact ───────────────────────────────────────────────────────

function Contact() {
  const [form, setForm] = useState({ name: "", email: "", company: "", message: "" });
  const [sent, setSent] = useState(false);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    // Placeholder — integrar com e-mail ou CRM futuramente
    setSent(true);
  }

  return (
    <section id="contact" className="bg-gray-900/50 py-24 border-t border-gray-800">
      <div className="mx-auto max-w-7xl px-6">
        <div className="grid lg:grid-cols-2 gap-16">
          <div>
            <span className="text-xs font-semibold uppercase tracking-widest text-brand-500">Contato</span>
            <h2 className="mt-3 text-3xl sm:text-4xl font-bold text-white">Vamos conversar?</h2>
            <p className="mt-4 text-gray-400 leading-relaxed">
              Quer uma demonstração, tem dúvidas técnicas ou quer saber como o
              FactoryConnect se encaixa na sua operação? Fale com a gente.
            </p>
            <div className="mt-8 space-y-4">
              {[
                { icon: "M21.75 6.75v10.5a2.25 2.25 0 0 1-2.25 2.25h-15a2.25 2.25 0 0 1-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0 0 19.5 4.5h-15a2.25 2.25 0 0 0-2.25 2.25m19.5 0v.243a2.25 2.25 0 0 1-1.07 1.916l-7.5 4.615a2.25 2.25 0 0 1-2.36 0L3.32 8.91a2.25 2.25 0 0 1-1.07-1.916V6.75", label: "contato@factoryconnect.tech" },
                { icon: "M2.25 6.75c0 8.284 6.716 15 15 15h2.25a2.25 2.25 0 0 0 2.25-2.25v-1.372c0-.516-.351-.966-.852-1.091l-4.423-1.106c-.44-.11-.902.055-1.173.417l-.97 1.293c-.282.376-.769.542-1.21.38a12.035 12.035 0 0 1-7.143-7.143c-.162-.441.004-.928.38-1.21l1.293-.97c.363-.271.527-.734.417-1.173L6.963 3.102a1.125 1.125 0 0 0-1.091-.852H4.5A2.25 2.25 0 0 0 2.25 6z", label: "+55 (92) 99999-0000" },
              ].map((c) => (
                <div key={c.label} className="flex items-center gap-3 text-sm text-gray-400">
                  <span className="flex size-9 items-center justify-center rounded-xl bg-gray-800 text-brand-400 shrink-0">
                    <Icon d={c.icon} size={4} />
                  </span>
                  {c.label}
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-2xl border border-gray-800 bg-gray-900 p-8">
            {sent ? (
              <div className="flex flex-col items-center justify-center h-full py-8 text-center gap-4">
                <div className="flex size-14 items-center justify-center rounded-full bg-brand-500/20 text-brand-400">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="size-7">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z" />
                  </svg>
                </div>
                <h3 className="text-lg font-semibold text-white">Mensagem enviada!</h3>
                <p className="text-sm text-gray-400">Nossa equipe entrará em contato em até 24h.</p>
                <button onClick={() => setSent(false)} className="text-sm text-brand-400 hover:text-brand-300">Enviar outra mensagem</button>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs text-gray-400 mb-1.5">Seu nome</label>
                    <input required value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                      placeholder="João Silva"
                      className="w-full rounded-xl border border-gray-700 bg-gray-800 px-4 py-2.5 text-sm text-gray-100 placeholder-gray-600 focus:border-brand-500 focus:outline-none" />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-400 mb-1.5">Empresa</label>
                    <input value={form.company} onChange={(e) => setForm((f) => ({ ...f, company: e.target.value }))}
                      placeholder="Sua Empresa Ltda"
                      className="w-full rounded-xl border border-gray-700 bg-gray-800 px-4 py-2.5 text-sm text-gray-100 placeholder-gray-600 focus:border-brand-500 focus:outline-none" />
                  </div>
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1.5">E-mail</label>
                  <input required type="email" value={form.email} onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
                    placeholder="joao@empresa.com"
                    className="w-full rounded-xl border border-gray-700 bg-gray-800 px-4 py-2.5 text-sm text-gray-100 placeholder-gray-600 focus:border-brand-500 focus:outline-none" />
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1.5">Mensagem</label>
                  <textarea required rows={4} value={form.message} onChange={(e) => setForm((f) => ({ ...f, message: e.target.value }))}
                    placeholder="Conte como podemos ajudar..."
                    className="w-full rounded-xl border border-gray-700 bg-gray-800 px-4 py-2.5 text-sm text-gray-100 placeholder-gray-600 focus:border-brand-500 focus:outline-none resize-none" />
                </div>
                <button type="submit"
                  className="w-full rounded-xl bg-brand-500 py-3 text-sm font-semibold text-white hover:bg-brand-600 transition-colors">
                  Enviar mensagem
                </button>
              </form>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

// ── Footer ────────────────────────────────────────────────────────

function Footer() {
  return (
    <footer className="bg-gray-950 border-t border-gray-800 py-10">
      <div className="mx-auto max-w-7xl px-6 flex flex-col sm:flex-row items-center justify-between gap-4">
        <div className="flex items-center gap-2.5">
          <span className="flex size-7 items-center justify-center rounded-lg bg-brand-500">
            <svg viewBox="0 0 24 24" fill="currentColor" className="size-4 text-white">
              <path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" />
            </svg>
          </span>
          <span className="text-sm font-semibold text-gray-400">FactoryConnect</span>
        </div>
        <p className="text-xs text-gray-600">© 2026 FactoryConnect. Todos os direitos reservados.</p>
        <div className="flex gap-4 text-xs text-gray-600">
          <Link to="/login" className="hover:text-gray-400">Login</Link>
          <Link to="/register" className="hover:text-gray-400">Cadastro</Link>
        </div>
      </div>
    </footer>
  );
}

// ── Page ──────────────────────────────────────────────────────────

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-gray-950 font-sans">
      <Navbar />
      <Hero />
      <Features />
      <Cases />
      <About />
      <Contact />
      <Footer />
    </div>
  );
}
