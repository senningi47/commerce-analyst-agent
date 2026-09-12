# Throwaway runtime for the Day 1 BIRD database-isolation spike.
# Build with scripts/spikes as the context so evaluator-only data can never
# enter the Docker build context.
# Base image digest pinned at the Task 12 preflight (2026-09-12, linux/amd64
# via the daemon's registry mirrors).
FROM python:3.11-slim-bookworm@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir \
        "fastapi>=0.110.0" \
        "uvicorn[standard]>=0.29.0" \
        "pydantic-settings>=2.2.0" \
        "python-dotenv>=1.0.0" \
        "psycopg2-binary>=2.9.9" \
        "sqlglot>=23.0.0"

WORKDIR /opt/bird-adk

CMD ["python", "-m", "db_environment.server"]
