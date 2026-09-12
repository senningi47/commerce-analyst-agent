# bird_system_agent image — independent build context per spec v0.3 §21/§5.1.
# Build from the repository root so the allowlisted src/configs/cache paths
# resolve; the root .dockerignore is the GT-isolation guarantee (never rely on
# it alone — the Task 6 contract tests assert the file list too).
#
# MUST-PIN before any real build (Task 12 preflight): Docker Hub was
# unreachable when this file was authored, so the base image tag is not yet
# digest-pinned. Run `docker buildx imagetools inspect python:3.11-slim`,
# replace the tag with python:3.11-slim@sha256:<digest>, and record it in the
# run manifest. Never build for the Pilot with a floating tag.
FROM python:3.11-slim

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
