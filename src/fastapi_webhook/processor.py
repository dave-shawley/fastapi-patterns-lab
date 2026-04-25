import asyncio
import logging
import types
import typing as t
from collections import abc

import fastapi
import pydantic

from fastapi_webhook import lifespan, utilities

if t.TYPE_CHECKING:
    import starlette.types


class State:
    def __init__(self, app: fastapi.FastAPI) -> None:
        self.active_tasks: set[asyncio.Task[None]] = set()
        self.app = app
        self.logger = logging.getLogger(__package__).getChild('State')

    def schedule_webhook(
        self,
        task_name: str,
        receive_message: starlette.types.Receive,
        scope: starlette.types.Scope,
    ) -> asyncio.Task[None]:
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
        task.add_done_callback(self._task_finished)

        return task

    def _task_finished(self, task: asyncio.Task[None]) -> None:
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

    async def __aenter__(self) -> State:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: types.TracebackType,
    ) -> object:
        for task in self.active_tasks:
            task.cancel()
        await asyncio.gather(*self.active_tasks, return_exceptions=True)
        return None  # propagate exception


type ProcessMessage = t.Callable[[str, str, pydantic.BaseModel], None]


def _get_webhook_task_runner(
    request: fastapi.Request, lifespan_map: lifespan.LifespanMap
) -> ProcessMessage:
    def process_message(
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

        state = lifespan_map.get_state(State)
        state.schedule_webhook(task_name, receive_message, scope)

    return process_message


WebhookTaskRunner = t.Annotated[
    ProcessMessage, fastapi.Depends(_get_webhook_task_runner)
]


async def _blackhole_send(_message: starlette.types.Message) -> None:
    """A no-op send function that discards all messages.

    This is essential since FastAPI will generate a response to our
    synthetic webhook requests and there is no where for it to go.
    """
