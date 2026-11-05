FROM python:3.11-slim AS base

WORKDIR /app

# Install shared schemas first (cacheable layer)
COPY packages/shared-schemas /tmp/shared-schemas
RUN pip install --no-cache-dir /tmp/shared-schemas && rm -rf /tmp/shared-schemas

# Install service dependencies
COPY services/orchestrator/pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application code
COPY services/orchestrator/app ./app

CMD ["celery", "-A", "app.celery_app:celery", "worker", "--loglevel=info", "--concurrency=2"]
