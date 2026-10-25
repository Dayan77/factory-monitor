# Imagens com versão fixa — atualize de propósito (Dependabot: .github/dependabot.yml).
FROM node:22.23.3-alpine AS dev
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host"]

FROM node:22.23.3-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.31.0-alpine AS prod
# Aplica todas as correções de segurança do Alpine já publicadas que a tag base ainda não traz
# (o Trivy no CI reprova HIGH/CRITICAL com correção disponível: libexpat, pcre2, libx11, tiff…).
RUN apk upgrade --no-cache
ARG FM_VERSION=dev
LABEL org.opencontainers.image.title="factoryconnect-web" \
      org.opencontainers.image.description="App web do FactoryConnect (React, servido por nginx)" \
      org.opencontainers.image.version="${FM_VERSION}" \
      org.opencontainers.image.source="https://github.com/Dayan77/factory-monitor"
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY nginx/security-headers.conf /etc/nginx/snippets/security-headers.conf
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD wget -qO- http://127.0.0.1/healthz >/dev/null || exit 1
