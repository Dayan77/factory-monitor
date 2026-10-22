/**
 * Client-side password hints (the server — services/security/passwords.py — is the authority: it also checks the
 * blocklist of leaked passwords and the history). These checks only spare a round trip for the obvious cases.
 */
export const PASSWORD_MIN = 12;

export interface PasswordCheck {
  ok: boolean;
  /** First problem found, in Portuguese (null when ok). */
  problem: string | null;
}

function trivial(p: string): boolean {
  if (new Set(p.toLowerCase()).size <= 2 || /^\d+$/.test(p)) return true;
  const codes = Array.from(p).map((c) => c.charCodeAt(0));
  const asc = codes.every((c, i) => i === 0 || c - codes[i - 1] === 1);
  const desc = codes.every((c, i) => i === 0 || codes[i - 1] - c === 1);
  return asc || desc;
}

/** Same rules as passwords.check_policy for length, trivial sequences and personal data (name, e-mail, company). */
export function checkPassword(password: string, context: (string | null | undefined)[] = []): PasswordCheck {
  if (password.length < PASSWORD_MIN) {
    return { ok: false, problem: `A senha deve ter no mínimo ${PASSWORD_MIN} caracteres.` };
  }
  if (trivial(password)) return { ok: false, problem: "Senha muito previsível (caracteres repetidos ou sequência)." };
  const low = password.toLowerCase();
  for (const item of context) {
    if (!item) continue;
    for (const part of item.toLowerCase().split(/[@.\s_+-]+/)) {
      if (part.length >= 4 && low.includes(part)) {
        return { ok: false, problem: "A senha não pode conter seu nome, e-mail ou o nome da empresa." };
      }
    }
  }
  return { ok: true, problem: null };
}

/** Rough strength meter (0–4) for the UI bar; never a reason to refuse a password the server accepts. */
export function passwordStrength(password: string): 0 | 1 | 2 | 3 | 4 {
  if (!password) return 0;
  let score = 0;
  if (password.length >= PASSWORD_MIN) score += 1;
  if (password.length >= 16) score += 1;
  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((r) => r.test(password)).length;
  if (classes >= 3) score += 1;
  if (password.length >= 20 || (classes === 4 && password.length >= 14)) score += 1;
  if (trivial(password)) score = 0;
  return Math.min(4, score) as 0 | 1 | 2 | 3 | 4;
}

/** Recovery codes / TOTP: digits only for a 6-digit code, keeping what the user pasted otherwise. */
export function normalizeOtp(input: string): string {
  return input.replace(/\s+/g, "").replace(/-/g, "");
}
