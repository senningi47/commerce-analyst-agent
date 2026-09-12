# Throwaway-pattern runtime for the official BIRD user simulator (pinned source).
# Built with scripts/spikes as the context; the official shared/ and
# user_simulator/ sources are mounted read-only at runtime, never baked.
# Base image digest pinned at the Task 12 preflight (2026-09-12, linux/amd64
# via the daemon's registry mirrors).
FROM python:3.11-slim-bookworm@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir \
    "fastapi>=0.110.0" \
    "uvicorn[standard]>=0.29.0" \
    "pydantic-settings>=2.2.0" \
    "python-dotenv>=1.0.0" \
    "litellm>=1.40.0" \
    "httpx>=0.27.0"

WORKDIR /opt/bird-adk

CMD ["python", "-m", "user_simulator.server"]
