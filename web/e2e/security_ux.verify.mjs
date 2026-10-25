/**
 * Sprint 7 — security UX against a REAL backend (no mocks), on a throwaway user so the run is repeatable:
 *   1. admin invites (Configurações → Usuários e convites) and gets the one-time link
 *   2. the invitee opens /convite, creates a password and lands signed in
 *   3. Minha conta: activates MFA (QR + key, TOTP computed here), receives recovery codes; exports the data (JSON
 *      download); sees ≥ 2 sessions and ends the other device; changes the password (policy + other sessions end)
 *   4. login now asks for the authenticator code (and accepts a recovery code); wrong code → message
 *   5. "Esqueci minha senha" → the same answer whatever the e-mail
 *   6. session expiry: a revoked session sends the user to /login?next=… with the "sessão expirou" notice and
 *      back to the same screen after signing in
 *   7. delete account (password + EXCLUIR) → login refused afterwards
 *   light + dark, desktop + phone screenshots of every screen; zero console errors.
 *
 *   BASE_URL=http://localhost:5250 API=http://localhost:8150 node e2e/security_ux.verify.mjs
 */
import { chromium } from "/opt/node-tools/node_modules/playwright/index.mjs";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.BASE_URL ?? "http://localhost:5250";
const API = process.env.API ?? "http://localhost:8150";
const ADMIN = { email: process.env.E2E_EMAIL ?? "admin@factory-monitor.com", password: process.env.E2E_PASSWORD ?? "Admin@1234" };
const OUT = process.env.OUT ?? path.join(path.dirname(new URL(import.meta.url).pathname), "screenshots/security");
fs.mkdirSync(OUT, { recursive: true });
let failures = 0;
const check = (c, m, got) => { console.log(`  ${c ? "✓" : "✗"} ${m}${c || got === undefined ? "" : `   got=${JSON.stringify(got).slice(0, 400)}`}`); if (!c) failures += 1; };

// ── TOTP (RFC 6238, SHA-1, 6 digits, 30 s) ──────────────────────────
function base32(s) {
  const A = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of s.replace(/=+$/, "").toUpperCase()) bits += A.indexOf(c).toString(2).padStart(5, "0");
  const out = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) out.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(out);
}
function totp(secret, at = Date.now()) {
  const step = Math.floor(at / 1000 / 30);
  const msg = Buffer.alloc(8);
  msg.writeBigUInt64BE(BigInt(step));
  const h = crypto.createHmac("sha1", base32(secret)).update(msg).digest();
  const o = h[h.length - 1] & 0xf;
  return String(((h.readUInt32BE(o) & 0x7fffffff) % 1_000_000)).padStart(6, "0");
}
/** A code from a time step never used before (the server refuses a replayed step). */
async function freshCode(secret, used) {
  for (;;) {
    const step = Math.floor(Date.now() / 30000);
    if (!used.has(step)) { used.add(step); return totp(secret); }
    await new Promise((r) => setTimeout(r, 1000));
  }
}

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
async function ctxFor(vp = "desktop", scheme = "light") {
  const ctx = await browser.newContext({
    ...(vp === "phone" ? { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } : { viewport: { width: 1366, height: 860 } }),
    locale: "pt-BR", timezoneId: "America/Sao_Paulo", colorScheme: scheme, acceptDownloads: true,
  });
  await ctx.addInitScript((s) => { try { localStorage.setItem("fm-theme", s); } catch { /* */ } }, scheme);
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    // the flows below provoke expected 4xx answers (wrong code, revoked session, deleted account)
    if (/Failed to load resource: the server responded with a status of (400|401|403|404|422)/.test(m.text())) return;
    errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(String(e)));
  return { ctx, page, errors };
}
const shot = (page, name) => page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true });

const stamp = Date.now().toString(36);
// passwords never contain a piece of the e-mail (the policy refuses it): random suffix instead of the stamp
const rnd = () => crypto.randomBytes(6).toString("base64url").replace(/[-_]/g, "x");
const NEW = { email: `qa-sec-${stamp}@example.com`, name: "Rita Qualidade", password: `Bancada-Verde-${rnd()}-42` };
const NEW_PW2 = `Esteira-Azul-${rnd()}-77`;

// 1. invitation
console.log("\n▶ convite (admin)");
const admin = await ctxFor("desktop", "light");
let inviteUrl;
{
  const { page } = admin;
  await page.goto(`${BASE}/login`);
  await page.fill("#login-email", ADMIN.email);
  await page.fill("#login-password", ADMIN.password);
  await page.getByRole("button", { name: "Entrar" }).click();
  await page.waitForURL(/\/dashboard/);
  await page.goto(`${BASE}/settings/users`);
  await page.getByLabel("E-mail").fill(NEW.email);
  await page.getByLabel("Nome (opcional)").fill(NEW.name);
  await page.getByRole("checkbox", { name: "Operador" }).uncheck();
  await page.getByRole("checkbox", { name: "Técnico de manutenção" }).check();
  await page.getByRole("button", { name: "Enviar convite" }).click();
  const box = page.getByTestId("invite-link");
  await box.waitFor({ timeout: 10000 });
  inviteUrl = (await box.locator("code").innerText()).trim();
  check(/\/convite\?token=/.test(inviteUrl), "convite criado; link exibido uma vez", inviteUrl.slice(0, 60));
  await page.getByText(NEW.email).first().waitFor();
  await shot(page, "01-users-invite-light");
  check(admin.errors.length === 0, "admin: sem erros de console", admin.errors);
}

// 2. accept
console.log("\n▶ aceitar convite (novo usuário, celular escuro)");
const u = await ctxFor("phone", "dark");
{
  const { page } = u;
  const tokenUrl = new URL(inviteUrl);
  await page.goto(`${BASE}/convite${tokenUrl.search}`);
  await page.getByTestId("invite-form").waitFor();
  check(!page.url().includes("token="), "token removido da barra de endereço");
  check(await page.getByText(NEW.email).isVisible(), "convite mostra o e-mail convidado");
  await shot(page, "02-invite-phone-dark");
  await page.getByLabel("Crie uma senha").fill(NEW.password);
  await page.getByLabel("Confirme a senha").fill(NEW.password);
  await page.getByRole("button", { name: "Criar conta e entrar" }).click();
  await page.waitForURL(/\/(dashboard|m\/work-orders)/, { timeout: 15000 });
  check(true, `convite aceito → ${new URL(page.url()).pathname}`);
}

// 3. account: MFA, export, sessions, password
console.log("\n▶ Minha conta (desktop claro)");
const acc = await ctxFor("desktop", "light");
const usedSteps = new Set();
let secret;
let recovery = [];
{
  const { page } = acc;
  await page.goto(`${BASE}/login`);
  await page.fill("#login-email", NEW.email);
  await page.fill("#login-password", NEW.password);
  await page.getByRole("button", { name: "Entrar" }).click();
  await page.waitForURL(/\/(dashboard|m\/)/);
  await page.goto(`${BASE}/account`);
  await page.getByTestId("mfa-status").waitFor();
  await shot(page, "03-account-light");
  // MFA
  await page.getByRole("button", { name: "Ativar verificação em duas etapas" }).click();
  await page.locator("#mfa-pw").fill(NEW.password);
  await page.getByRole("button", { name: "Continuar" }).click();
  secret = (await page.getByTestId("mfa-secret").innerText()).trim();
  check(/^[A-Z2-7]{16,}$/.test(secret), "chave TOTP exibida", secret.length);
  await page.getByRole("img", { name: /QR code/ }).locator("svg").waitFor({ timeout: 5000 });
  check(true, "QR code desenhado");
  await shot(page, "04-mfa-enroll-light");
  await page.getByLabel("Código de 6 dígitos").fill(await freshCode(secret, usedSteps));
  await page.getByRole("button", { name: "Ativar", exact: true }).click();
  const codes = page.getByTestId("recovery-codes");
  await codes.waitFor();
  recovery = (await codes.locator("li").allInnerTexts()).map((s) => s.trim());
  check(recovery.length === 10, "10 códigos de recuperação exibidos", recovery.length);
  check(/Ativa desde/.test(await page.getByTestId("mfa-status").innerText()), "MFA ativa");
  await shot(page, "05-mfa-on-light");
  // export
  const [dl] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Baixar meus dados" }).click()]);
  const file = await dl.path();
  const data = JSON.parse(fs.readFileSync(file, "utf8"));
  check(JSON.stringify(data).includes(NEW.email), "exportação LGPD: JSON com os dados do usuário", dl.suggestedFilename());
  // sessions: the phone context (accept) + this one
  await page.reload();
  const rows = page.getByTestId("session-row");
  await rows.first().waitFor();
  const n = await rows.count();
  check(n >= 2, `sessões ativas listadas (${n})`);
  await page.getByRole("button", { name: /Encerrar as outras/ }).click();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="session-row"]').length === 1, null, { timeout: 10000 });
  check(true, "outras sessões encerradas (fica só esta)");
  // password change: policy refused locally, then a valid one
  await page.locator("#pw-current").fill(NEW.password);
  await page.locator("#pw-new").fill("ritaqualidade-2026-x");
  await page.locator("#pw-confirm").fill("ritaqualidade-2026-x");
  await page.getByRole("button", { name: "Trocar senha" }).click();
  check(await page.getByText(/não pode conter seu nome/).isVisible(), "senha com o nome recusada antes do servidor");
  await page.locator("#pw-new").fill(NEW_PW2);
  await page.locator("#pw-confirm").fill(NEW_PW2);
  await page.getByRole("button", { name: "Trocar senha" }).click();
  await page.getByText("Senha alterada.").waitFor({ timeout: 10000 });
  check(true, "senha trocada");
  check(acc.errors.length === 0, "Minha conta: sem erros de console", acc.errors);
}

// 6. the phone session was ended above → next API call there = expired session flow
console.log("\n▶ sessão encerrada em outro dispositivo (celular)");
{
  const { page } = u;
  await page.goto(`${BASE}/maintenance/work-orders?view=list`);
  await page.waitForURL(/\/login\?next=/, { timeout: 15000 });
  check(decodeURIComponent(new URL(page.url()).searchParams.get("next") ?? "").startsWith("/maintenance/work-orders"), "volta para /login?next=<tela atual>", page.url());
  check(await page.getByTestId("session-expired").waitFor({ timeout: 8000 }).then(() => true, () => false), "aviso 'Sua sessão expirou'");
  await shot(page, "06-session-expired-phone-dark");
  // 4. login with MFA (wrong code first, then a recovery code)
  await page.fill("#login-email", NEW.email);
  await page.fill("#login-password", NEW_PW2);
  await page.getByRole("button", { name: "Entrar" }).click();
  await page.getByTestId("mfa-step").waitFor();
  await shot(page, "07-login-mfa-phone-dark");
  await page.getByLabel("Código do autenticador").fill("000000");
  await page.getByRole("button", { name: "Verificar" }).click();
  check(await page.getByText("Código inválido").waitFor({ timeout: 8000 }).then(() => true, () => false), "código errado → 'Código inválido'");
  await page.getByRole("button", { name: "Usar um código de recuperação" }).click();
  await page.getByLabel("Código de recuperação").fill(recovery[0]);
  await page.getByRole("button", { name: "Verificar" }).click();
  await page.waitForURL(/\/maintenance\/work-orders/, { timeout: 15000 });
  check(true, "código de recuperação aceito → de volta à tela em que estava");
  check(u.errors.length === 0, "celular: sem erros de console", u.errors);
}

// 5. forgot password
console.log("\n▶ esqueci minha senha");
{
  const { page, errors } = await ctxFor("phone", "light");
  await page.goto(`${BASE}/login`);
  await page.getByRole("link", { name: "Esqueci minha senha" }).click();
  await page.getByLabel("E-mail da sua conta").fill(`ninguem-${stamp}@example.com`);
  await page.getByRole("button", { name: "Enviar link" }).click();
  await page.getByTestId("reset-sent").waitFor();
  const a = await page.getByTestId("reset-sent").innerText();
  await page.getByRole("button", { name: "Enviar novamente" }).click();
  await page.getByLabel("E-mail da sua conta").fill(NEW.email);
  await page.getByRole("button", { name: "Enviar link" }).click();
  await page.getByTestId("reset-sent").waitFor();
  const b = await page.getByTestId("reset-sent").innerText();
  check(a.replace(/\S+@\S+/, "") === b.replace(/\S+@\S+/, ""), "mesma resposta para e-mail existente e inexistente");
  await shot(page, "08-forgot-phone-light");
  await page.goto(`${BASE}/redefinir-senha?token=invalido`);
  await page.getByLabel("Nova senha", { exact: true }).fill("Bancada-Verde-Seguro-42");
  await page.getByLabel("Confirme a nova senha").fill("Bancada-Verde-Seguro-42");
  await page.getByRole("button", { name: "Salvar nova senha" }).click();
  await page.getByTestId("reset-invalid").waitFor();
  check(true, "link inválido → 'Pedir um novo link'");
  check(errors.length === 0, "sem erros de console", errors);
}

// 7. delete account
console.log("\n▶ excluir conta");
{
  const { page } = acc;
  await page.goto(`${BASE}/account#privacidade`);
  await page.getByRole("button", { name: "Excluir conta…" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Senha atual").fill(NEW_PW2);
  await dialog.getByLabel(/para confirmar/).fill("EXCLUIR");
  await shot(page, "09-delete-light");
  await dialog.getByRole("button", { name: "Excluir definitivamente" }).click();
  await page.waitForURL(/\/login\?deleted=1/, { timeout: 15000 });
  check(true, "conta excluída → login com aviso");
  const r = await fetch(`${API}/api/auth/login`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email: NEW.email, password: NEW_PW2 }) });
  check(r.status === 401, "login da conta excluída é recusado", r.status);
  check(acc.errors.length === 0, "sem erros de console", acc.errors);
}

// dark desktop / light phone screenshots of the account + login screens
for (const [vp, scheme] of [["desktop", "dark"], ["phone", "light"]]) {
  const { page, errors } = await ctxFor(vp, scheme);
  await page.goto(`${BASE}/login`);
  await shot(page, `10-login-${vp}-${scheme}`);
  await page.fill("#login-email", ADMIN.email);
  await page.fill("#login-password", ADMIN.password);
  await page.getByRole("button", { name: "Entrar" }).click();
  await page.waitForURL(/\/dashboard/);
  await page.goto(`${BASE}/account`);
  await page.getByTestId("mfa-status").waitFor();
  await shot(page, `11-account-${vp}-${scheme}`);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check(overflow <= 1, `${vp}/${scheme}: Minha conta sem rolagem horizontal`, overflow);
  check(errors.length === 0, `${vp}/${scheme}: sem erros de console`, errors);
}

await browser.close();
console.log(failures ? `\nFAIL — ${failures}` : "\nPASS");
process.exit(failures ? 1 : 0);
