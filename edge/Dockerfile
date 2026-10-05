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

# /data: mapa provisionado, buffer e contadores de borda (volume nomeado herda o dono).
RUN useradd --system --uid 10001 --no-create-home edge && mkdir -p /data && chown -R edge /app /data
USER edge

# Gateway por padrão; planta virtual: python -m virtual_plant; provisionamento: python -m src.provision
CMD ["python", "-m", "src.gateway"]
