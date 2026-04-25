# vim: set et:
FROM python:3.14-alpine AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_PROGRESS=1 \
    UV_LOCKED=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/app

WORKDIR /source

# Optimized build - install dependencies before copying source and
# installing project gives us a clean docker layer that only needs
# to be rebuilt when pyproject.toml or uv.lock is updated
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
RUN uv venv /app
RUN --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --active --no-install-project --no-editable

# Copy the source and install the application
COPY . /source
RUN uv sync --active --no-editable --all-extras --no-dev --no-default-groups

#-----------------------------------------------------------------------

FROM python:3.14-alpine

ENV PATH=/app/bin:$PATH \
    OTEL_LOGS_EXPORTER=none \
    OTEL_METRICS_EXPORTER=none \
    OTEL_TRACES_EXPORTER=none \
    OTEL_SERVICE_NAME=fastapi-patterns-lab \
    UVICORN_HOST=0.0.0.0 \
    UVICORN_WORKERS=1 \
    UVICORN_WS=none \
    UVICORN_PROXY_HEADERS=1 \
    UVICORN_SERVER_HEADER=0
EXPOSE 8000

COPY --from=builder /app /app
CMD ["/app/bin/opentelemetry-instrument", "/app/bin/uvicorn", "--factory", "fastapi_webhook.entrypoints:create_app"]
