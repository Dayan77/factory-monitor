# Privacidade e LGPD — FactoryConnect

Este documento descreve quais dados pessoais a plataforma trata, por quanto tempo os guarda e como o titular
exerce seus direitos (Lei 13.709/2018 — LGPD). Implementação: `server/src/services/security/privacy.py`,
`server/src/api/routes/account.py`; testes: `server/tests/test_security_privacy.py`.

Papéis: o **cliente (tenant)** é o *controlador* dos dados dos seus usuários e da operação da fábrica; a
FactoryConnect é *operadora*. Para o formulário do site público (leads) a FactoryConnect é controladora.

## 1. Dados pessoais tratados

| Dado | Onde | Finalidade | Base legal (art. 7º) |
|---|---|---|---|
| Nome, e-mail, papéis, linhas, plantão | `users` | conta e permissões, escala de manutenção | execução de contrato |
| Hash da senha (bcrypt), histórico das 5 últimas | `users`, `password_history` | autenticação, política de senha | execução de contrato / segurança |
| Segredo TOTP (cifrado AES-256-GCM) e códigos de recuperação (hash) | `users`, `mfa_recovery_codes` | MFA | segurança (legítimo interesse) |
| Sessões: início, último uso, prefixo do IP (/24), navegador | `auth_sessions` | lista "meus dispositivos", revogação | segurança |
| Trilha de auditoria (quem fez o quê) | `audit_events` | segurança, rastreabilidade de manutenção | obrigação/legítimo interesse |
| OS, apontamentos, justificativas de parada, falhas | tabelas de manutenção/produção | operação da fábrica | execução de contrato |
| Conversas com o assistente de IA | `ai_conversations`, `ai_messages` | histórico do usuário | execução de contrato |
| Notificações, preferências, inscrições de push | `notifications`, `notification_preferences`, `push_subscriptions` | avisos | execução de contrato |
| Leads do site (nome, e-mail, empresa, telefone, mensagem, consentimento) | `leads` | contato comercial | consentimento |
| IPs de contadores anti-abuso | `auth_throttle`, `rate_counters`, `leads.ip_hash` | anti força bruta / spam | legítimo interesse |

Nunca guardamos IP completo: contadores usam HMAC-SHA256 do IP com a chave da aplicação; sessões guardam só o
prefixo `/24` (`/48` em IPv6). Senhas, tokens e segredos não vão para logs nem para a auditoria.

## 2. Direito de acesso e portabilidade — exportação

`GET /api/account/export` (usuário autenticado, na própria conta) devolve um JSON
(`factoryconnect.personal-data-export/v1`) com: perfil, papéis, sessões, eventos de auditoria do usuário (até
5 000 mais recentes), preferências e inscrições de notificação, notificações (1 000 mais recentes), conversas com o
assistente, OS em que é responsável ou autor, ausências e convites enviados. A exportação é auditada
(`account.data_exported`). Tokens de API e a demonstração pública não acessam essa área.

## 3. Direito de eliminação — anonimização

* **Autoatendimento**: `DELETE /api/account` com `{"password", "confirm": "EXCLUIR"}`. O último administrador ativo
  de um tenant precisa promover outro antes; superadmin não se exclui por aqui.
* **Pelo administrador do tenant**: `POST /api/users/{id}/anonymize`.

A conta é **pseudonimizada**, não apagada: o registro do usuário permanece para que a trilha de auditoria, as OS,
falhas, justificativas de parada e o histórico de manutenção (necessários para MTBF/MTTR e rastreabilidade —
ISO 14224) continuem consistentes, agora ligados a "Usuário removido xxxxxxxx". São apagados ou zerados:
e-mail (substituído por `removido-…@anonimizado.invalid`), nome, senha e histórico de senhas, MFA e códigos,
sessões (revogadas; IP e navegador apagados), tokens de redefinição, notificações, preferências, inscrições de push,
conversas com o assistente e notas de ausência. A ação é irreversível e auditada (`account.deleted` /
`user.anonymized`).

## 4. Retenção

Tarefa periódica `privacy_janitor` (a cada `PRIVACY_JANITOR_INTERVAL_S`, padrão 1 h):

| Dado | Retenção | Configuração |
|---|---|---|
| Leads do site | 365 dias | `LEAD_RETENTION_DAYS` (0 = manter) |
| Eventos de auditoria | 5 anos | `AUDIT_RETENTION_DAYS` (0 = manter) |
| Sessões expiradas/revogadas (e seus refresh tokens) | 30 dias após expirar/revogar | — |
| Tokens de redefinição de senha | 1 dia após expirar | — |
| Convites não aceitos | 30 dias após expirar | — |
| Contadores de força bruta / limite de taxa | 1 dia | — |
| Conversas do assistente de IA | 90 dias | `AI_RETENTION_DAYS` (módulo de IA) |
| Sessões da demonstração pública | sem dado pessoal (só hash do IP) | — |

Dados de operação da fábrica (séries temporais, OS, falhas) seguem o contrato do cliente e são apagados com o
tenant.

## 5. Configurações de privacidade e segurança

* `LEAD_RETENTION_DAYS`, `AUDIT_RETENTION_DAYS`, `PRIVACY_JANITOR_INTERVAL_S` — retenção (acima).
* `MFA_ENCRYPTION_KEY` — chave de cifra dos segredos TOTP (padrão: derivada de `APP_SECRET_KEY`; defina uma
  própria em produção para poder girar a chave JWT sem perder o MFA).
* Política do tenant `PUT /api/account/tenant-security` `{"require_mfa_admins": true}` — exige MFA dos
  administradores.

## 6. Incidentes

Revogação imediata: `POST /api/auth/logout-all` (o próprio usuário), `POST /api/users/{id}/sessions/revoke`
(administrador), suspensão do tenant pelo superadmin (encerra todas as sessões do tenant), rotação da chave JWT
(`JWT_KEYS` / `JWT_ACTIVE_KID`, ver `docs/security.md`). A trilha `audit_events` registra login, falhas, bloqueios,
MFA, mudanças de papel, tokens, webhooks e exportações para a apuração.
