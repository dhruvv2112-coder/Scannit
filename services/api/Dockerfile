FROM python:3.11-slim AS base

WORKDIR /app

# Install shared schemas first (cacheable layer)
COPY packages/shared-schemas /tmp/shared-schemas
RUN pip install --no-cache-dir /tmp/shared-schemas && rm -rf /tmp/shared-schemas

# Install service dependencies
COPY services/api/pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application code
COPY services/api/app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
