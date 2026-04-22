# syntax=docker/dockerfile:1.6

# ── Stage 1: build the Svelte frontend ────────────────────────────────
FROM node:20-alpine AS frontend-build

WORKDIR /build

# Copy only manifest + lockfile first so npm ci is cached independently of src changes
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build


# ── Stage 2: Python runtime ───────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# System deps for bcrypt, psycopg2, healthcheck curl
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev curl && \
    rm -rf /var/lib/apt/lists/*

# Non-root runtime user (uid 10001 to avoid host-user collisions)
RUN groupadd --system --gid 10001 app && \
    useradd --system --uid 10001 --gid app --home-dir /home/app --create-home --shell /usr/sbin/nologin app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the Python source
COPY . .

# Replace any locally-built frontend/dist with the fresh one from the build stage
RUN rm -rf frontend/dist
COPY --from=frontend-build /build/dist ./frontend/dist

# Runtime-writable directories (volume mounts cover these in prod)
RUN mkdir -p data/users plugins && chown -R app:app /app

# No .pyc clutter — keeps read-only root filesystems happy
ENV PYTHONDONTWRITEBYTECODE=1

USER app

EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
