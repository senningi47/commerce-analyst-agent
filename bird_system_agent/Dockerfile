# bird_system_agent image — independent build context per spec v0.3 §21/§5.1.
# Build from the repository root so the allowlisted src/configs/cache paths
# resolve; the root .dockerignore is the GT-isolation guarantee (never rely on
# it alone — the Task 6 contract tests assert the file list too).
#
# Base image digest pinned at the Task 12 preflight (2026-09-12): the digest
# is the local pull digest for linux/amd64 resolved through the daemon's
# registry mirrors (Docker Hub direct was unreachable); it pins the exact
# base-image content this machine's Pilot builds against. Never build with a
# floating tag.
FROM python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY bird_system_agent/requirements.txt /app/bird_system_agent/requirements.txt
RUN pip install --no-cache-dir -r /app/bird_system_agent/requirements.txt

COPY pyproject.toml /app/pyproject.toml
COPY src/commerce_agent /app/src/commerce_agent
COPY configs/model /app/configs/model
COPY bird_system_agent /app/bird_system_agent
COPY .cache/commerce-agent/deepseek-tokenizer /app/.cache/commerce-agent/deepseek-tokenizer

RUN useradd --uid 10001 --create-home bird
USER bird

EXPOSE 6000
CMD ["python", "-m", "bird_system_agent.server"]
