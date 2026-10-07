# Site público (factoryconnect.tech): build estático com Astro, servido por nginx.
# Imagens com versão fixa — atualize de propósito (Dependabot: .github/dependabot.yml).
FROM node:22.23.3-alpine AS build
WORKDIR /app
ENV ASTRO_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# URLs do app/API embutidas no build (o site é estático)
ARG PUBLIC_APP_URL=https://app.factoryconnect.tech
ARG PUBLIC_API_URL=https://app.factoryconnect.tech
ARG PUBLIC_CONTACT_EMAIL=
ENV PUBLIC_APP_URL=$PUBLIC_APP_URL PUBLIC_API_URL=$PUBLIC_API_URL PUBLIC_CONTACT_EMAIL=$PUBLIC_CONTACT_EMAIL
RUN npm run build
# nginx final: CSP com o hash de cada script inline do build + origem da API no connect-src
RUN node scripts/render-nginx.mjs --dist dist --connect-src "$PUBLIC_API_URL" --out /app/nginx-out

FROM nginx:1.30.5-alpine AS prod
# Correções de segurança do Alpine ainda não incluídas na imagem base (Trivy, Sprint 7: libexpat CVE-2026-93990,
# pcre2 CVE-2026-103111). Remova quando a tag base nova já trouxer as versões corrigidas.
RUN apk add --no-cache --upgrade libexpat pcre2
ARG FM_VERSION=dev
LABEL org.opencontainers.image.title="factoryconnect-site" \
      org.opencontainers.image.description="Site público do FactoryConnect (Astro estático, nginx)" \
      org.opencontainers.image.version="${FM_VERSION}" \
      org.opencontainers.image.source="https://github.com/Dayan77/factory-monitor"
COPY --from=build /app/nginx-out/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/nginx-out/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD wget -qO- http://127.0.0.1/healthz >/dev/null || exit 1
