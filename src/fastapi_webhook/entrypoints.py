import fastapi

from fastapi_webhook import meta


@cli.callback()
def _doc_stub() -> None:
    """FastAPI Webhook processor"""
    # this exists to ensure that sub-command processing is consistent
    # whether there are zero, one, or many sub-commands


def create_app() -> fastapi.FastAPI:
    app = fastapi.FastAPI()
    app.include_router(meta.router)
    return app
