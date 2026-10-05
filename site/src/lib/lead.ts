// Validação e envio do formulário de contato (POST /api/public/leads).
// Módulo puro (sem DOM) para ser testado com vitest.

export interface LeadInput {
  name: string;
  email: string;
  company: string;
  phone: string;
  message: string;
  consent: boolean;
  website: string; // honeypot — precisa ficar vazio
}

export interface LeadPayload {
  name: string;
  email: string;
  company: string | null;
  phone: string | null;
  message: string | null;
  consent: boolean;
  website: string;
  source: string;
}

export type FieldErrors = Partial<Record<keyof LeadInput, string>>;

export const LIMITS = { name: 255, email: 254, company: 255, phone: 64, message: 5000 } as const;

// Pragmático: algo@dominio.tld, sem espaços. O servidor faz a validação definitiva.
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const PHONE_RE = /^[0-9+()\-.\s]{8,}$/;

export function validateLead(input: LeadInput): FieldErrors {
  const e: FieldErrors = {};
  const name = input.name.trim();
  const email = input.email.trim();
  const phone = input.phone.trim();
  const message = input.message.trim();

  if (!name) e.name = "Informe seu nome.";
  else if (name.length < 2) e.name = "Nome muito curto.";
  else if (name.length > LIMITS.name) e.name = `Máximo de ${LIMITS.name} caracteres.`;

  if (!email) e.email = "Informe seu e-mail.";
  else if (email.length > LIMITS.email || !EMAIL_RE.test(email)) e.email = "E-mail inválido.";

  if (input.company.trim().length > LIMITS.company) e.company = `Máximo de ${LIMITS.company} caracteres.`;

  if (phone) {
    const digits = phone.replace(/\D/g, "");
    if (phone.length > LIMITS.phone || !PHONE_RE.test(phone) || digits.length < 8 || digits.length > 15) {
      e.phone = "Telefone inválido.";
    }
  }

  if (!message) e.message = "Conte em poucas palavras o que você precisa.";
  else if (message.length > LIMITS.message) e.message = `Máximo de ${LIMITS.message} caracteres.`;

  if (!input.consent) e.consent = "É necessário concordar com o tratamento dos dados para enviarmos uma resposta.";

  return e;
}

const orNull = (s: string) => (s.trim() ? s.trim() : null);

export function toPayload(input: LeadInput, source: string): LeadPayload {
  return {
    name: input.name.trim(),
    email: input.email.trim(),
    company: orNull(input.company),
    phone: orNull(input.phone),
    message: orNull(input.message),
    consent: input.consent,
    website: input.website,
    source: source.slice(0, 255),
  };
}

export type SubmitResult =
  | { ok: true }
  | { ok: false; kind: "validation" | "rate_limit" | "server" | "network"; message: string };

type FetchLike = (url: string, init: RequestInit) => Promise<Pick<Response, "ok" | "status" | "json">>;

export async function submitLead(
  endpoint: string,
  payload: LeadPayload,
  fetchImpl: FetchLike = fetch,
  timeoutMs = 15000,
): Promise<SubmitResult> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetchImpl(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
      credentials: "omit",
      signal: ctrl.signal,
    });
    if (res.ok) return { ok: true };
    if (res.status === 422 || res.status === 400) {
      return { ok: false, kind: "validation", message: "Alguns dados não foram aceitos. Revise os campos e tente de novo." };
    }
    if (res.status === 429) {
      return { ok: false, kind: "rate_limit", message: "Recebemos várias mensagens deste endereço. Tente novamente mais tarde." };
    }
    return { ok: false, kind: "server", message: "Não foi possível enviar agora. Tente novamente em alguns minutos." };
  } catch {
    return { ok: false, kind: "network", message: "Falha de conexão. Verifique sua internet e tente novamente." };
  } finally {
    clearTimeout(timer);
  }
}
