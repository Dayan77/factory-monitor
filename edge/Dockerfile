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

RUN useradd --system --uid 10001 --no-create-home edge && chown -R edge /app
USER edge

CMD ["python", "-m", "src.gateway"]
