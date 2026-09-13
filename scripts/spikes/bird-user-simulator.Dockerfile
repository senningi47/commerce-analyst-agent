# Throwaway-pattern runtime for the official BIRD user simulator (pinned source).
# Built with scripts/spikes as the context; the official shared/ and
# user_simulator/ sources are mounted read-only at runtime, never baked.
# Base image digest pinned at the Task 12 preflight (2026-09-12, linux/amd64
# via the daemon's registry mirrors).
#
# Install list FULLY PINNED at Task 13 pre-run (2026-09-13): the floating
# ranges of the Task 12 build are replaced by the exact set installed in the
# working 2026-09-12 image (captured via `pip freeze` inside that image), so a
# rebuild cannot drift the litellm/fastapi combination the official simulator
# code was last verified against. sqlglot==30.17.0 is the one addition — the
# first real `compose up` exposed ModuleNotFoundError (sqlglot) because the
# official user_simulator imports it but the Task 12 image never installed it.
FROM python:3.11-slim-bookworm@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir \
    aiohappyeyeballs==2.7.1 \
    aiohttp==3.14.3 \
    aiosignal==1.4.0 \
    annotated-doc==0.0.5 \
    annotated-types==0.8.0 \
    anyio==4.15.1 \
    attrs==26.1.0 \
    boto3==1.43.93 \
    botocore==1.43.93 \
    certifi==2026.7.22 \
    charset-normalizer==3.5.1 \
    click==8.5.0 \
    distro==1.9.0 \
    fastapi==0.141.1 \
    fastuuid==0.14.0 \
    filelock==3.32.6 \
    frozenlist==1.8.0 \
    fsspec==2026.7.0 \
    h11==0.16.0 \
    hf-xet==1.6.0 \
    httpcore==1.0.9 \
    httptools==0.8.0 \
    httpx==0.28.1 \
    huggingface_hub==1.31.0 \
    idna==3.19 \
    importlib_metadata==8.9.0 \
    Jinja2==3.1.6 \
    jiter==0.16.0 \
    jmespath==1.1.0 \
    jsonschema==4.26.0 \
    jsonschema-specifications==2025.9.1 \
    litellm==1.100.1 \
    MarkupSafe==3.0.3 \
    multidict==6.8.0 \
    openai==2.54.0 \
    packaging==26.3 \
    propcache==0.5.2 \
    pydantic==2.13.5 \
    pydantic-settings==2.15.0 \
    pydantic_core==2.46.5 \
    python-dateutil==2.9.0.post0 \
    python-dotenv==1.2.3 \
    PyYAML==6.0.3 \
    referencing==0.37.0 \
    regex==2026.9.10 \
    requests==2.34.2 \
    rpds-py==2026.6.3 \
    s3transfer==0.19.2 \
    six==1.17.0 \
    sniffio==1.3.1 \
    sqlglot==30.17.0 \
    starlette==1.6.0 \
    tiktoken==0.14.0 \
    tokenizers==0.23.2 \
    tqdm==4.70.1 \
    typing-inspection==0.4.4 \
    typing_extensions==4.16.0 \
    urllib3==2.7.0 \
    uvicorn==0.52.4 \
    uvloop==0.22.1 \
    watchfiles==1.2.0 \
    websockets==17.1 \
    yarl==1.24.5 \
    zipp==4.1.0

WORKDIR /opt/bird-adk

CMD ["python", "-m", "user_simulator.server"]
