import fastapi
import typer

from fastapi_webhook import github, lifespan, meta, pagerduty, processor

cli = typer.Typer(no_args_is_help=True)
cli.add_typer(pagerduty.cli)


@cli.callback()
def _doc_stub() -> None:
    """FastAPI Webhook processor"""
    # this exists to ensure that sub-command processing is consistent
    # whether there are zero, one, or many sub-commands


def create_app() -> fastapi.FastAPI:
    lifespan_mgr = lifespan.Lifespan(processor.State)
    app = fastapi.FastAPI(lifespan=lifespan_mgr)
    app.include_router(github.router)
    app.include_router(meta.router)
    app.include_router(pagerduty.router)
    return app
