# Site público — factoryconnect.tech

Site institucional estático (Astro + Tailwind v4), separado do app (`app.factoryconnect.tech`) para SEO e desempenho.
Sem rastreadores de terceiros; o formulário de contato envia para `POST {PUBLIC_API_URL}/api/public/leads`.

```bash
npm ci
npm run dev                 # http://localhost:4321
npx astro check && npx vitest run && npx astro build
node tests/e2e.mjs          # Playwright: todas as páginas desktop + mobile, formulário, tema, SEO → test-results/screens/
python3 tests/traefik_routing.py   # roteamento de domínios do compose de produção com Traefik v3 e v2 (docker)
node scripts/gen-assets.mjs # regenera favicon.ico, ícones PNG e og.png em public/
```

Variáveis de build (`PUBLIC_*`, embutidas no HTML): `PUBLIC_APP_URL` e `PUBLIC_API_URL`
(padrão `https://app.factoryconnect.tech`), `PUBLIC_CONTACT_EMAIL` (opcional — sem ele o site não exibe e-mail).

Conteúdo: só afirmações de capacidade verificáveis. Nada de depoimentos, nomes de clientes ou números inventados;
a seção de cases só entra quando houver cases reais autorizados.

Deploy: `site/Dockerfile` (build estático → nginx 1.30.5, `nginx.conf` como template com o CSP `connect-src`
apontando para a API) e serviço `site` em `infra/docker-compose.prod.yml` (Traefik: apex → site, www → apex,
rotas antigas do app no apex → `app.factoryconnect.tech`).
