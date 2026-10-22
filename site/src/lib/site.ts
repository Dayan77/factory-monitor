// Configuração central do site. URLs do app e da API vêm de variáveis PUBLIC_* (build time).
const env = import.meta.env;

const trimSlash = (u: string) => u.replace(/\/+$/, "");

export const SITE_URL = "https://factoryconnect.tech";
export const APP_URL = trimSlash(env.PUBLIC_APP_URL || "https://app.factoryconnect.tech");
export const API_URL = trimSlash(env.PUBLIC_API_URL || APP_URL);
export const LEADS_ENDPOINT = `${API_URL}/api/public/leads`;
// E-mail público de contato: só aparece se configurado (não inventamos endereços).
export const CONTACT_EMAIL: string = env.PUBLIC_CONTACT_EMAIL || "";

export const BRAND = "FactoryConnect";
export const TAGLINE = "O raio-x da sua fábrica";
export const DEFAULT_DESCRIPTION =
  "FactoryConnect conecta máquinas e sensores da sua fábrica e mostra eficiência (OEE), manutenção (MTBF/MTTR) e o uso real de cada componente — em tempo real, sem trocar o que já funciona.";

// "Ver demonstração" (src/lib/demo.ts): o site só consulta o status; o app abre a sessão na rota /demo.
export const DEMO_URL = `${APP_URL}/demo`;
export const DEMO_STATUS_URL = `${API_URL}/api/public/demo-status`;
// Endpoint first-party opcional do evento anônimo "abriu demo" (vazio = nenhum evento sai do navegador).
export const DEMO_EVENT_URL: string = env.PUBLIC_DEMO_EVENT_URL || "";

export const LOGIN_URL = `${APP_URL}/login`;
export const REGISTER_URL = `${APP_URL}/register`;

export interface NavItem { href: string; label: string; /** Rótulo curto no menu do topo entre 1024 e 1279 px */ short?: string }

export const NAV: NavItem[] = [
  { href: "/solucao", label: "Solução" },
  { href: "/para-manutencao", label: "Manutenção" },
  { href: "/para-producao", label: "Produção" },
  { href: "/como-funciona", label: "Como funciona" },
  { href: "/seguranca", label: "Segurança" },
  { href: "/planos", label: "Planos" },
  { href: "/docs", label: "Manual" },
];

export const FOOTER_NAV: { title: string; items: NavItem[] }[] = [
  {
    title: "Produto",
    items: [
      { href: "/solucao", label: "Solução" },
      { href: "/como-funciona", label: "Como funciona" },
      { href: "/seguranca", label: "Segurança" },
      { href: "/planos", label: "Planos" },
      { href: "/docs", label: "Documentação" },
    ],
  },
  {
    title: "Para quem",
    items: [
      { href: "/para-manutencao", label: "Manutenção" },
      { href: "/para-producao", label: "Produção" },
    ],
  },
  {
    title: "Empresa",
    items: [
      { href: "/sobre", label: "Sobre" },
      { href: "/contato", label: "Contato" },
      { href: "/privacidade", label: "Política de Privacidade" },
    ],
  },
];
