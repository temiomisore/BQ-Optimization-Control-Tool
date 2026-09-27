# ---------- Stage 1: build the React review UI (frontend/ -> /web/dist) ----------
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: Python runtime (Flask API + classic UI + built React assets) ----------
FROM python:3.12-slim
WORKDIR /app

# Install OpenJDK Headless JRE + libstdc++6 + curl for Google's Official ZetaSQL Anti-Pattern Recognition Engine
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-jre-headless \
    libstdc++6 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Download Google's Official BigQuery Anti-Pattern Recognition JAR (v1.0.0.1)
RUN curl -L -s -o /app/bigquery-antipattern-recognition.jar \
    https://github.com/GoogleCloudPlatform/bigquery-antipattern-recognition/releases/download/v1.0.0.1/bigquery-antipattern-recognition.jar

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY optimizer/ optimizer/
COPY review_app/ review_app/
COPY sql/ sql/
COPY config.yaml* config.yaml.example ./
RUN if [ ! -f config.yaml ]; then cp config.yaml.example config.yaml; fi

# React build output, served by Flask at "/" (classic Jinja UI stays at "/classic")
COPY --from=web /web/dist /app/frontend/dist

# Run container as non-root user (CIS Docker Benchmark & Google Cloud WAF Security Pillar)
RUN useradd -m -u 10001 appuser && chown -R appuser:appuser /app
USER appuser

# Multi-threaded Gunicorn worker config for concurrent Cloud Run requests + BigQuery dry-run latency.
# Timeout 900s: a W-01 reservation rollback waits for queries to route back to on-demand (can exceed 2 min).
CMD ["gunicorn", "--bind", ":8080", "--workers", "2", "--threads", "8", "--timeout", "900", "review_app.main:app"]
