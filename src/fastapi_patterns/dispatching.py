"""Helpers for redispatching validated payloads through FastAPI.

Use [DispatchState][.DispatchState] in conjunction with the
[Lifespan][fastapi_patterns.lifespan.Lifespan] manager to enable
dispatching of new requests through the FastAPI machinery complete
with routing, middleware execution, and dependency injection. This
enables request processing similar to [fastapi.BackgroundTasks][]
except that it creates a new ASGI scope and clean dependency chain.

"""

from __future__ import annotations

import asyncio
import logging
import types
import typing as t
from collections import abc

import fastapi
import pydantic

from fastapi_patterns import utilities

if t.TYPE_CHECKING:
    import starlette.types

    from fastapi_patterns import lifespan


class DispatchState:
    """
    Manages the dispatching and lifecycle of asynchronous tasks.

    This class provides functionality to schedule, track, and clean up
    asynchronous tasks linked to specific requests in the application.
    It integrates with the FastAPI lifecycle, logging task-related
    events, and ensuring proper resource cleanup.
    """

    def __init__(self, app: fastapi.FastAPI) -> None:
        self.active_tasks: set[asyncio.Task[None]] = set()
        self.app = app
        self.logger = logging.getLogger(__package__).getChild('DispatchState')

    def schedule_request(
        self,
        task_name: str,
        receive_message: starlette.types.Receive,
        scope: starlette.types.Scope,
    ) -> asyncio.Task[None]:
        """Schedule a request task for execution by the application.

        A new asyncio task is created and scheduled by calling the
        application instance with the provided scope and message generator.
        Response handling is disabled by passing a blackhole send function.
        Task completion is handled by registering
        [task_finished][..task_finished] as a callback.

        Args:
            task_name: name of the task to schedule
            receive_message: ASGI message generator
            scope: ASGI scope
        """
        self.logger.info(
            'scheduling task %r for scope %r',
            task_name,
            {
                'type': scope['type'],
                'path': scope['path'],
                'root_path': scope['root_path'],
                'method': scope['method'],
            },
        )

        task = asyncio.create_task(
            self.app(scope, receive_message, _blackhole_send), name=task_name
        )
        self.active_tasks.add(task)
        task.add_done_callback(self.task_finished)

        return task

    def task_finished(self, task: asyncio.Task[None]) -> None:
        """Handle task completion.

        Simple function that ensures that task completion is logged
        particularly when an exception has occurred.
        """
        task_name = utilities.get_task_name(task)
        try:
            if task.cancelled():
                self.logger.warning('task %r cancelled', task_name)
            elif exc := task.exception():
                self.logger.error(
                    'task %r failed',
                    task_name,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
            else:
                self.logger.info('task %r finished', task_name)
        finally:
            self.active_tasks.discard(task)

    async def __aenter__(self) -> DispatchState:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_traceback: types.TracebackType | None,
    ) -> object:
        for task in self.active_tasks:
            task.cancel()
        await asyncio.gather(*self.active_tasks, return_exceptions=True)
        return None  # propagate exception


type DispatchMessage = t.Callable[[str, str, pydantic.BaseModel], None]
"""Callable for dispatching a new ASGI message.

Use the [DispatchTaskRunner][..DispatchTaskRunner] dependency to inject
this into anywhere that FastAPI's dependency injection is available.
The callable creates an independent ASGI scope and schedules a new task
using the [DispatchState][..DispatchState.schedule_request] lifespan
object.

Args:
    task_name(str): name to assign to the new task
    path(str): path to the target endpoint
    payload(pydantic.BaseModel): validated request payload

"""


def _get_dispatch_task_runner(
    request: fastapi.Request, lifespan_map: lifespan.LifespanMap
) -> DispatchMessage:
    def dispatch_message(
        task_name: str, path: str, payload: pydantic.BaseModel
    ) -> None:
        body = payload.model_dump_json().encode()
        parent_state = t.cast(
            'abc.Mapping[str, t.Any]', request.scope.get('state', {})
        )
        scope: starlette.types.Scope = {
            'type': 'http',
            'asgi': {'version': '3.0'},
            'http_version': '1.1',
            'method': 'POST',
            'path': path,
            'raw_path': path.encode(),
            'query_string': b'',
            'headers': [
                (b'content-length', str(len(body)).encode()),
                (b'content-type', b'application/json'),
                *[
                    (k.lower().encode(), v.encode())
                    for k, v in request.headers.items()
                    if k.lower() not in ('content-length', 'content-type')
                ],
            ],
            'client': ('internal', 0),
            'server': ('internal', 0),
            'scheme': 'http',
            'root_path': '',
            'app': request.app,
            'state': dict(parent_state),
        }

        async def receive_message() -> starlette.types.Message:
            return {'type': 'http.request', 'body': body, 'more_body': False}

        state = lifespan_map.get_state(DispatchState)
        state.schedule_request(task_name, receive_message, scope)

    return dispatch_message


DispatchTaskRunner = t.Annotated[
    DispatchMessage, fastapi.Depends(_get_dispatch_task_runner)
]
"""Dependency injection for [DispatchMessage][..DispatchMessage]."""


async def _blackhole_send(_message: starlette.types.Message) -> None:
    """Discard the synthetic response generated by the internal request."""
