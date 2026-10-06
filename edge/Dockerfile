# syntax=docker/dockerfile:1
# check=skip=SecretsUsedInArgOrEnv
# Gateway de campo FactoryConnect (edge/) — imagem multi-arquitetura: linux/amd64 (IPC/PC industrial) e
# linux/arm64 (Raspberry Pi 4/5 com SO 64 bits, IPCs ARM). Mesma imagem serve a planta virtual e o provisionamento
# (infra/docker-compose.yml, profile "sim").
#
#   docker buildx build --platform linux/amd64,linux/arm64 -t <registro>/factoryconnect-edge:0.5.0 \
#       --build-arg EDGE_VERSION=0.5.0 --push edge/
#
# Wheels binários (manylinux) para TODAS as dependências nas duas arquiteturas (--only-binary): o build não precisa
# de gcc/apt nem de emulação para compilar nada (com QEMU/binfmt o buildx só roda o pip do arm64).
ARG PYTHON_IMAGE=python:3.12.15-slim
FROM ${PYTHON_IMAGE}

ARG EDGE_VERSION=0.5.0
ARG TARGETPLATFORM
LABEL org.opencontainers.image.title="factoryconnect-edge" \
      org.opencontainers.image.description="Gateway de campo FactoryConnect (Modbus TCP, OPC UA, MQTT, buffer offline)" \
      org.opencontainers.image.version="${EDGE_VERSION}" \
      org.opencontainers.image.source="https://github.com/Dayan77/factory-monitor"

# Estado persistente do gateway, tudo no volume /data (0700, dono = usuário edge).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    EDGE_VERSION=${EDGE_VERSION} \
    BUFFER_DB_PATH=/data/edge_buffer.db \
    COUNTER_DB_PATH=/data/edge_counters.db \
    GATEWAY_CONFIG_CACHE=/data/gateway_config.json \
    GATEWAY_CREDENTIALS_PATH=/data/gateway_credentials.json \
    HEALTH_FILE=/data/gateway.health

WORKDIR /app

COPY requirements.txt .
RUN pip install --only-binary=:all: -r requirements.txt

COPY . .

# /data: credencial do cadastro (0600), cópia da configuração, buffer e contadores de borda.
RUN useradd --system --uid 10001 --no-create-home edge \
    && mkdir -p /data && chown -R edge /app /data && chmod 0700 /data \
    && echo "${EDGE_VERSION} ${TARGETPLATFORM:-local}" > /app/BUILD
VOLUME ["/data"]
USER edge

# O gateway não abre portas de entrada; a saúde é o arquivo tocado a cada heartbeat (heartbeat_s, padrão 10 s).
# (Os serviços da planta virtual/provisionamento do compose de dev definem a própria verificação ou não precisam.)
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os,sys,time; p=os.environ.get('HEALTH_FILE') or '/data/gateway.health'; sys.exit(0 if os.path.exists(p) and time.time() - os.path.getmtime(p) < 120 else 1)"

# Gateway por padrão; planta virtual: python -m virtual_plant; provisionamento: python -m src.provision;
# cadastro manual: python -m src.enrollment --server https://... --code XXXX-XXXX-XXXX
CMD ["python", "-m", "src.gateway"]
