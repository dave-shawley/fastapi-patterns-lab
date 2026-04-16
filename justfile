@help:
    just --list

format:
    uv run ruff format
    -uv run pre-commit run tombi-format
    just --fmt --unstable

serve:
    -uv run uvicorn --factory fastapi_webhook.entrypoints:create_app --log-config log-config.yaml --reload
