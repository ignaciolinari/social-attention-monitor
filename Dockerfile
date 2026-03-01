FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps (minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install project
COPY pyproject.toml README.md LICENSE /app/
COPY src /app/src
COPY alembic.ini /app/
COPY .env.example /app/.env.example

RUN python -m pip install --upgrade pip && \
    python -m pip install .

# Run as non-root user
RUN groupadd --gid 1000 sam && \
    useradd --uid 1000 --gid sam --shell /bin/sh sam && \
    chown -R sam:sam /app
USER sam

EXPOSE 8000 8501

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
