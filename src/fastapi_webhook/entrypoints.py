import fastapi
import typer

from fastapi_webhook import meta, pagerduty

cli = typer.Typer(no_args_is_help=True)
cli.add_typer(pagerduty.cli)


@cli.callback()
def _doc_stub() -> None:
    """FastAPI Webhook processor"""
    # this exists to ensure that sub-command processing is consistent
    # whether there are zero, one, or many sub-commands


def create_app() -> fastapi.FastAPI:
    app = fastapi.FastAPI()
    app.include_router(meta.router)
    app.include_router(pagerduty.router)
    return app
