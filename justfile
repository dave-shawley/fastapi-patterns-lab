export UV_FROZEN := "1"

@help:
    just --list

[arg('FILES', pattern='.*\.py')]
[doc("Reformat source files")]
format *FILES:
    uv run --no-sync ruff format {{ FILES }}
    -uv run --no-sync pre-commit run tombi-format
    just --fmt --unstable

[doc("Run style checkers and static analyzers")]
lint:
    uv run ruff check
    uv run mypy -p fastapi_webhook

[doc("Run the service using uvicorn")]
serve *ARGS:
    touch .env
    -uv run --env-file .env uvicorn --factory fastapi_webhook.entrypoints:create_app --log-config log-config.yaml --reload {{ ARGS }}
