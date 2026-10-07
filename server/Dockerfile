# Wheels binários (manylinux) para todas as dependências: não precisa de gcc/apt.
FROM python:3.12.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --only-binary=:all: -r requirements.txt

COPY . .

# data/files = FILES_DIR (volume server-files no compose de produção): existe na imagem com dono app, então o
# volume nomeado novo nasce gravável pelo uid 10001.
RUN useradd --system --uid 10001 --no-create-home app && mkdir -p /app/data/files && chown -R app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=3)" || exit 1
# Proxy headers are handled inside the app (src/main.py, ProxyHeadersMiddleware with
# FORWARDED_ALLOW_IPS = the proxy IPs/CIDRs): the client IP is the right-most X-Forwarded-For
# entry that is not a trusted proxy, so a spoofed left-most entry cannot bypass the per-IP lead
# rate limit. Never use --forwarded-allow-ips "*" (uvicorn then trusts the client-controlled entry).
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
