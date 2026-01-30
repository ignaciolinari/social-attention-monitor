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

EXPOSE 8000 8501
