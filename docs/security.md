# Segurança da aplicação — FactoryConnect

Endurecimento da Fase 6 (Sprint 7, backend B). Código: `server/src/auth/`, `server/src/services/security/`,
`server/src/api/routes/{auth,account,users}.py`; migration `d7e8f9a0b1c2`. Testes: `server/tests/test_security_*.py`.
Privacidade/LGPD: `docs/privacy.md`. Infraestrutura (TLS, nginx, MQTT, segredos do CI): fora deste documento.

## 1. Autenticação

### 1.1 Tokens e sessões
* **Access token** JWT curto: 15 min (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`). Claims: `sub`, `tenant_id`, `role`,
  `sid` (sessão), `iat`, `jti`, `typ=access`; cabeçalho `kid`.
* **Refresh token** (7 dias, `JWT_REFRESH_TOKEN_EXPIRE_DAYS`) de uso único: `POST /api/auth/refresh` devolve um par
  novo e consome o anterior (**rotação**). Reapresentar um refresh já usado fora da janela de graça
  (`REFRESH_REUSE_GRACE_S`, 10 s — duas abas renovando ao mesmo tempo) é tratado como roubo: **a família inteira
  (sessão) é revogada** e o evento `auth.refresh_reuse` vai para a auditoria. Refresh tokens antigos (sem `sid`) são
  recusados: o usuário entra de novo uma vez após o deploy.
* **Sessões no servidor** (`auth_sessions`): cada login cria uma; cada access token é checado contra ela a cada
  requisição (revogada/expirada → 401).
  * `GET /api/account/sessions` — dispositivos ativos (navegador, prefixo /24 do IP, último uso, `current`);
  * `DELETE /api/account/sessions/{id}` — encerra uma; `DELETE /api/account/sessions` — encerra as outras;
  * `POST /api/auth/logout` — encerra a atual; `POST /api/auth/logout-all` — **sair de todos os dispositivos**
    (todas as sessões + corte global `users.tokens_valid_after` para tokens sem sessão);
  * troca de senha encerra as outras sessões; redefinição de senha, reset de MFA pelo admin, desativação,
    anonimização e suspensão do tenant encerram todas.
* **Rotação de chave**: `JWT_KEYS="k2026a:<segredo>,k2026b:<segredo>"` + `JWT_ACTIVE_KID=k2026b`. A ativa assina, todas
  verificam; remover uma chave da lista invalida os tokens dela. Sem `JWT_KEYS`, a chave é `APP_SECRET_KEY` (kid
  `k0`). Tokens sem `kid` (antigos, sessão de demonstração, links assinados) são verificados com `APP_SECRET_KEY`.
* **Relógio**: tolerância `JWT_LEEWAY_S` (30 s) em `exp`/`nbf`.
* Tipos de token separados por `typ` (`access`, `refresh`, `mfa`, `reset`): nenhum é aceito no lugar de outro.
* **WebSocket** `/ws/sensors/{tenant_id}`: mesma validação do HTTP; a sessão é re-checada a cada
  `WS_REVALIDATE_S` (60 s) e na expiração do token (fecha com 4401). O cliente pode renovar sem reconectar enviando
  `{"type": "reauth", "token": "<novo access>"}` → `{"type": "reauth_ok", "expires_at"}` (token inválido ou de outro
  usuário → 4401).

### 1.2 Senhas
* Mínimo 12 caracteres (`PASSWORD_MIN_LENGTH`), máximo 72 bytes (limite do bcrypt).
* Lista offline de senhas comuns/vazadas (`services/security/common_passwords.txt` + `PASSWORD_BLOCKLIST_FILE`
  opcional, uma por linha — ex. extraída do HIBP), comparada após normalização: minúsculas, leetspeak desfeito,
  dígitos/símbolos nas pontas removidos (`Senha@2026!` e `P4ssw0rd!2026` são recusadas).
* Recusa caractere repetido, sequências, só dígitos, e senhas que contêm nome, e-mail ou nome da empresa.
* Sem reuso das **5 últimas** (`password_history`).
* bcrypt custo 12 (`BCRYPT_ROUNDS`, ~250 ms); hashes com custo menor são refeitos no próximo login.
* Aplica-se a: cadastro público, criação de usuário (admin e superadmin), convite, troca e redefinição.

### 1.3 Força bruta e limites de taxa
Contadores em Postgres (valem para todos os workers), chaves com HMAC (nunca e-mail/IP em claro):

| Proteção | Limite | Efeito |
|---|---|---|
| Falhas por conta (e-mail) | 5 em 15 min | bloqueio 30 s × 2ⁿ (máx. 1 h), mesmo com a senha certa |
| Falhas por IP | 20 em 15 min | idem por IP |
| Falhas de código MFA por usuário | 5 | idem |
| Tentativas de login por IP | 30/min | 429 |
| Pedidos de redefinição | 10/h por IP; 3/h por e-mail (silencioso) | 429 / ignorado |
| Confirmações de redefinição / convites / cadastro | 30/h, 60/h, 10/h por IP | 429 |
| Leads e sessões de demonstração (já existentes) | 5/h e 10/h por IP | 429 |

Resposta de bloqueio: 429 com `Retry-After`, mesma mensagem para e-mail inexistente (sem enumeração). Login com
e-mail inexistente também gasta um bcrypt (tempo igual). Auditoria: `auth.login`, `auth.login_failed`,
`auth.lockout`, `auth.login_blocked`.

### 1.4 MFA (TOTP, opcional)
* `POST /api/account/mfa/enroll {password}` → `{secret, otpauth_uri}` (o app desenha o QR code da URI);
  `POST /api/account/mfa/confirm {code}` → ativa e devolve **10 códigos de recuperação** (mostrados uma vez,
  guardados com hash). `POST /api/account/mfa/recovery-codes {code}` gera novos; `POST /api/account/mfa/disable
  {password, code}` desativa.
* Login com MFA: `POST /api/auth/login` → `{mfa_required: true, mfa_token, methods}`; `POST /api/auth/mfa/verify
  {mfa_token, code | recovery_code}` → tokens. O desafio vale 5 min e uma vez; um código TOTP nunca é aceito duas
  vezes (replay); ±30 s de tolerância.
* Segredo TOTP cifrado (AES-256-GCM; `MFA_ENCRYPTION_KEY` ou derivado de `APP_SECRET_KEY`).
* **Política do tenant**: `PUT /api/account/tenant-security {require_mfa_admins: true}` (admin com MFA ativo).
  Admin/superadmin sem MFA passa a receber 403 com `X-Error-Code: mfa_enrollment_required` em tudo, exceto
  `/api/auth/me`, logout e `/api/account/{mfa,sessions,me}` — o app leva direto ao cadastro do MFA.
* Admin pode zerar o MFA de quem perdeu o aparelho: `POST /api/users/{id}/mfa/reset` (auditado).

### 1.5 Redefinição de senha e convites
* `POST /api/auth/password-reset/request {email}` → sempre 202 com o mesmo corpo; se a conta existe, e-mail com
  link `…/redefinir-senha?token=` (JWT `typ=reset` assinado, 15 min, uso único — linha em `password_reset_tokens`;
  morre também se a senha mudar). `POST /api/auth/password-reset/confirm {token, new_password}` → 204, encerra todas
  as sessões.
* Convites: `POST /api/users/invitations {email, name?, role, roles?, line_ids?}` (admin) → e-mail com
  `…/convite?token=` (7 dias; `invite_url` também volta uma vez para o admin compartilhar). O convidado vê
  `GET /api/auth/invitations/{token}` e cria a conta em `POST /api/auth/invitations/accept {token, password, name?}`
  (já entra logado). O admin nunca conhece a senha. `GET/DELETE /api/users/invitations[/{id}]`.

## 2. Autorização

* Toda rota tem a política declarada como dado em `server/src/services/security/policy.py` (`POLICY`:
  `"MÉTODO /caminho" → spec`). Gramática: `public`, `gateway`, `user`, `manager`, `admin`, `superadmin`,
  `any(papel|…)`, `only(principal|…)`, modificadores `+noapi`, `+nodemo`.
* `tests/test_security_authz_matrix.py` gera a partir da tabela de rotas do FastAPI uma chamada de **cada rota** com:
  sem token, cada papel do tenant A (operador, técnico, supervisor de produção, planejador, gerente, admin,
  superadmin), visitante da demonstração, token de gateway, token de API `read` e `write`, e admin/operador do
  tenant B usando ids do tenant A. Afirma a matriz declarada (negado ⇒ nunca 2xx; sem token ⇒ 401; permitido ⇒
  nunca 401/403; nunca 500) e que **nenhuma resposta ao tenant B contém id ou nome do tenant A** (nem 204 em recurso
  do tenant A). Rota nova sem entrada na política reprova o teste.
* Tokens de API agem como planejador de manutenção do tenant; nunca acessam a área pessoal (`/api/account`), não
  gerenciam integrações, usuários, convites nem sessões.

## 3. Entrada e resposta

* **Tamanho**: corpo > 1 MiB → 413 (`MAX_BODY_BYTES`), por Content-Length ou contando o que chega; exceções
  explícitas para upload de imagem do layout (`LAYOUT_IMAGE_MAX_BYTES` + 1 MiB) e importação CSV (8 MiB).
* **Corpos estritos** (`extra="forbid"`) em autenticação, conta e usuários; o teste
  `test_security_write_bodies_forbid_extra_fields` impede que novas rotas fora da lista de transição aceitem campos
  desconhecidos (lista a reduzir, nunca aumentar).
* **Erros**: sempre `{"detail": …}`. Exceção não tratada → 500 `{"detail": "Erro interno do servidor.",
  "request_id"}` (traceback só no log). Erros de validação não devolvem o valor enviado (`input`), para não ecoar
  senhas.
* **Cabeçalhos**: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Permissions-Policy`, `Cross-Origin-Opener-Policy`, CSP `default-src 'none'; frame-ancestors 'none'` nas respostas
  da API (a documentação `/api/docs` mantém a sua), `Cache-Control: no-store` em auth/conta/usuários/tokens/gateways,
  HSTS em produção (`APP_ENV=production`).
* **CORS**: só origens exatas (`esquema://host[:porta]`); `*` ou curinga derrubam a inicialização.
* **Injeção**: SQL sempre parametrizado (teste estático recusa `text(f"…")`/`execute(f"…")`); Flux só interpola ids
  validados com `ensure_uuid` ou vindos de colunas UUID, durações validadas e datas RFC 3339.
* **SSRF**: webhooks recusam esquemas não-HTTP e hosts privados/loopback/link-local (inclusive metadados de nuvem),
  com o IP validado fixado na entrega (sem DNS rebinding) e sem seguir redirecionamentos; push só para os serviços
  de push conhecidos; a URL de controle da planta virtual vem só da configuração.

## 4. Auditoria (`audit_events`)

Eventos de segurança: `auth.login`, `auth.login_failed`, `auth.lockout`, `auth.login_blocked`, `auth.mfa_challenge`,
`auth.mfa_failed`, `auth.mfa_recovery_used`, `auth.refresh_reuse`, `auth.logout`, `auth.logout_all`,
`auth.password_reset_requested`, `auth.password_reset`, `account.*` (senha, MFA, sessões, exportação, exclusão),
`user.created`, `user.roles_changed`, `user.activation_changed`, `user.roster_changed`, `user.invitation_*`,
`user.mfa_reset`, `user.sessions_revoked`, `user.anonymized`, `user.deleted`, `tenant.*` (cadastro, alteração,
suspensão, exclusão, política de segurança), `api_token.created/revoked`, `webhook.created/updated/deleted`,
`report.exported`, `report.downloaded`, `gateway.enroll_code_created`, `gateway.enrolled`, `gateway.revoked`. Nunca guarda senha, token, segredo ou IP completo (só o prefixo /24).

## 5. Configuração (variáveis novas)

`JWT_KEYS`, `JWT_ACTIVE_KID`, `JWT_LEEWAY_S`, `REFRESH_REUSE_GRACE_S`, `WS_REVALIDATE_S`, `PASSWORD_MIN_LENGTH`,
`PASSWORD_HISTORY`, `BCRYPT_ROUNDS`, `PASSWORD_BLOCKLIST_FILE`, `LOGIN_WINDOW_S`, `LOGIN_MAX_FAILURES_ACCOUNT`,
`LOGIN_MAX_FAILURES_IP`, `LOGIN_LOCK_BASE_S`, `LOGIN_LOCK_MAX_S`, `LOGIN_RATE_PER_IP_PER_MIN`,
`PASSWORD_RESET_TTL_MIN`, `PASSWORD_RESET_PER_IP_PER_HOUR`, `PASSWORD_RESET_PER_EMAIL_PER_HOUR`,
`INVITE_TTL_HOURS`, `MFA_ISSUER`, `MFA_ENCRYPTION_KEY`, `MAX_BODY_BYTES`, `LEAD_RETENTION_DAYS`,
`AUDIT_RETENTION_DAYS`, `PRIVACY_JANITOR_INTERVAL_S`. Todos têm padrão seguro; em produção defina
`MFA_ENCRYPTION_KEY` e, para girar a chave JWT, `JWT_KEYS`/`JWT_ACTIVE_KID`.
