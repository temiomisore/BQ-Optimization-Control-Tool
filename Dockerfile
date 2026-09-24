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

# Run container as non-root user (CIS Docker Benchmark & Google Cloud WAF Security Pillar)
RUN useradd -m -u 10001 appuser && chown -R appuser:appuser /app
USER appuser

# Multi-threaded Gunicorn worker config for concurrent Cloud Run requests + BigQuery dry-run latency
CMD ["gunicorn", "--bind", ":8080", "--workers", "2", "--threads", "8", "--timeout", "120", "review_app.main:app"]

