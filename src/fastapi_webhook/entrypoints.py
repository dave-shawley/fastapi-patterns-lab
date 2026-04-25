import fastapi
import typer

from fastapi_patterns import dispatching, lifespan
from fastapi_webhook import github, meta, pagerduty

cli = typer.Typer(no_args_is_help=True)
cli.add_typer(pagerduty.cli)


@cli.callback()
def _doc_stub() -> None:
    """FastAPI patterns lab example application."""
    # this exists to ensure that sub-command processing is consistent
    # whether there are zero, one, or many sub-commands


def create_app() -> fastapi.FastAPI:
    lifespan_mgr = lifespan.Lifespan(dispatching.DispatchState)
    app = fastapi.FastAPI(lifespan=lifespan_mgr)
    app.include_router(github.router)
    app.include_router(meta.router)
    app.include_router(pagerduty.router)
    return app
