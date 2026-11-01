# ---------- Stage 1: build the React review UI (frontend/ -> /web/dist) ----------
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund --registry=https://registry.npmjs.org/
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: download & SHA-256 verify ZetaSQL Anti-Pattern JAR ----------
FROM python:3.12-slim AS jar
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*
ARG ANTIPATTERN_JAR_URL=https://github.com/GoogleCloudPlatform/bigquery-antipattern-recognition/releases/download/v1.0.0.1/bigquery-antipattern-recognition.jar
ARG ANTIPATTERN_JAR_SHA256=e0b135c8eec4d24dc4d8507eeefc93cdec6baf2c13261926c7c1809184b8f7e3
RUN curl -fsSL -o /tmp/bigquery-antipattern-recognition.jar "${ANTIPATTERN_JAR_URL}" \
    && echo "${ANTIPATTERN_JAR_SHA256}  /tmp/bigquery-antipattern-recognition.jar" | sha256sum -c -

# ---------- Stage 3: minimal Python runtime (immutable root-owned /app) ----------
FROM python:3.12-slim
WORKDIR /app

# Install OpenJDK Headless JRE + libstdc++6 only (no curl/fetch tools in runtime image)
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-jre-headless \
    libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=jar /tmp/bigquery-antipattern-recognition.jar /app/bigquery-antipattern-recognition.jar
COPY optimizer/ optimizer/
COPY review_app/ review_app/
COPY sql/ sql/
# Never bake local config.yaml into container images; runtime uses BQOPT_CONFIG_JSON + BQOPT_* env vars.
COPY config.yaml.example ./config.yaml

# React build output, served by Flask at "/" (classic Jinja UI stays at "/classic")
COPY --from=web /web/dist /app/frontend/dist

# Run container as unprivileged non-root user while keeping /app owned by root:root (read-only code)
RUN useradd -m -u 10001 -s /usr/sbin/nologin appuser && chmod -R a+rX /app
USER appuser

# Single-process multi-threaded Gunicorn config so all requests share the in-memory dashboard cache.
# Timeout 900s: a W-01 reservation rollback waits for queries to route back to on-demand (can exceed 2 min).
CMD ["gunicorn", "--bind", ":8080", "--workers", "1", "--threads", "16", "--timeout", "900", "review_app.main:app"]
