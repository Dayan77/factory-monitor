# Site público (factoryconnect.tech): build estático com Astro, servido por nginx.
# Imagens com versão fixa — atualize de propósito.
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

FROM nginx:1.30.5-alpine AS prod
# Origem da API liberada no CSP connect-src (formulário de contato)
ARG PUBLIC_API_URL=https://app.factoryconnect.tech
ENV SITE_CONNECT_SRC=$PUBLIC_API_URL
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD wget -qO- http://127.0.0.1/healthz >/dev/null || exit 1
