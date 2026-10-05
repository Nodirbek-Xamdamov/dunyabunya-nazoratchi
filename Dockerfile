FROM python:3.12-slim AS builder

WORKDIR /app

# Install system dependencies for cryptography
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Production stage
FROM python:3.12-slim

WORKDIR /app

# PDF hisobotlar uchun Unicode shrift (kirill va o'zbek harflari) — app/services/pdf_documents.py
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY app ./app
COPY scripts ./scripts
COPY tests ./tests
COPY pytest.ini ./pytest.ini

# Create storage and log directories
RUN mkdir -p /app/storage/reports /app/logs && chown -R appuser:appuser /app/storage /app/logs

USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8002/health\")"

EXPOSE 8002

CMD ["python", "-m", "app.main"]
