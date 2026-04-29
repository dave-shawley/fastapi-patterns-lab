# Dispatching Webhooks Internally

The [fastapi_patterns.dispatching][] module lets a public webhook
endpoint validate and normalize an external request, then re-enter the
FastAPI application with a new internal `http` request. The internal
request runs in its own task and gets normal routing, body parsing, and
dependency injection instead of sharing the original request object.

## Why this exists

Most public webhook endpoints have two jobs:

1. authenticate and normalize the incoming request
2. hand off the actual processing to a separate task

The second step matters because the processor path should not inherit
the original request object or its dependency instances. The goal is to
let the processing endpoint execute as if it were handling a new request
created inside the same application.

This pattern keeps the public endpoint fast while still letting the
processing endpoint use normal FastAPI machinery. The public route can
return quickly, and the internal route can still rely on middleware,
dependency injection, request parsing, and lifespan-managed state.

## See it in action

In this repository, both `fastapi_webhook.github` and
`fastapi_webhook.pagerduty` use this pattern. They accept an external
notification, construct a normalized pydantic payload, and redispatch
that payload to an internal `process_notification()` route.

## High-level flow

The concrete GitHub and PagerDuty implementations vary a bit, but the
flow is the same:

1. A public `POST /.../notification` route validates the webhook and
   builds a normalized payload object.
2. The route calls `run_webhook(...)`, which is injected by
   [dispatching.DispatchTaskRunner][fastapi_patterns.dispatching.DispatchTaskRunner].
3. `DispatchTaskRunner` builds a synthetic ASGI `http` scope and a
   synthetic `receive()` callable for that payload.
4. The dependency looks up the lifespan-managed
   [DispatchState][fastapi_patterns.dispatching.DispatchState] and asks
   it to schedule a new task.
5. `DispatchState.schedule_request()` calls
   `app(scope, receive, _blackhole_send)` in that task.
6. FastAPI routes the synthetic request to the internal processing
   endpoint.
7. `process_notification()` executes with a fresh request scope and a
   fresh dependency-resolution pass.

The public endpoint does not call the processing handler directly. It
re-enters the ASGI application, so routing, dependency injection, and
request parsing all happen again.

## How to use it

The normal pattern has four parts.

### 1. Register `DispatchState` in the app lifespan

`DispatchTaskRunner` depends on a lifespan-managed
`DispatchState`. Without this, there is nowhere to schedule the
synthetic request.

```python title="entrypoints.py"
import fastapi

from fastapi_patterns import dispatching, lifespan

app = fastapi.FastAPI(
    lifespan=lifespan.Lifespan(dispatching.DispatchState)
)
```

See [Composable FastAPI Lifespans](lifespan.md) for the details of that
lookup path.

### 2. Define an internal processing endpoint

Treat the internal route like a normal FastAPI endpoint. It should
accept the normalized payload that the public endpoint will pass to it.

```python title="webhook.py"
import fastapi
import pydantic

router = fastapi.APIRouter(prefix='/source')


class NormalizedPayload(pydantic.BaseModel):
    id: str
    kind: str


@router.post('/process/notification', include_in_schema=False)
async def process_notification(payload: NormalizedPayload) -> None:
    ...
```

### 3. Define the public webhook endpoint and inject `DispatchTaskRunner`

The public route is responsible for the external webhook contract:
signature checks, header inspection, body decoding, and payload
normalization.

```python title="webhook.py"
from fastapi_patterns.dispatching import DispatchTaskRunner


@router.post('/notification', status_code=204)
async def receive_notification(
    request: fastapi.Request,
    run_webhook: DispatchTaskRunner,
) -> None:
    normalized = NormalizedPayload.model_validate(await request.json())
    ...
```

### 4. Redispatch the normalized payload to the internal route

Call the injected runner with a task name, a route path, and the
normalized payload.

```python title="webhook.py"
    run_webhook(
        f'source-{normalized.id}',
        router.url_path_for('process_notification'),
        normalized,
    )
```

The important part is the path value. The synthetic ASGI scope needs the
route path string that FastAPI will match, not an absolute URL.
`router.url_path_for(...)` is the safest choice when the processing
route lives on the same router.

## When to reach for this helper

Use this pattern when all of these are true:

- the external webhook should acknowledge quickly
- the processing step should run as a normal FastAPI endpoint
- the processing endpoint needs its own dependency-resolution pass
- the processing path may depend on lifespan-managed state

If all you need is a background function call and not a new request
scope, a plain helper function or [fastapi.BackgroundTasks][] is
usually simpler.

## Implementation details

### What “fresh dependency chain” means here

FastAPI resolves dependencies per request. By creating a new ASGI scope
and routing it back through the app, the processor gets:

- a new `Request` instance
- a new dependency-resolution pass
- a new body parsing pass
- a separate `asyncio` task

It does not create a second application. App-level state, lifespan
resources, router configuration, and middleware stack are shared.

### Where the active pieces live

If you need to trace the runtime path, the important files are:

- `src/fastapi_webhook/entrypoints.py`
  wires `fastapi.FastAPI(lifespan=lifespan.Lifespan(dispatching.DispatchState))`
- `src/fastapi_patterns/lifespan.py`
  provides the request-time access to lifespan-managed state
- `src/fastapi_patterns/dispatching.py`
  builds the synthetic scope and schedules `app(scope, receive, send)`
- `src/fastapi_webhook/github.py` and `src/fastapi_webhook/pagerduty.py`
  are the concrete public and internal route examples

### Required invariants

If any of these are broken, the dispatch may fail before the processing
handler runs.

#### 1. The synthetic scope must be a valid HTTP scope

At a minimum it needs the fields Starlette and FastAPI expect for an
HTTP request, including:

- `type='http'`
- `method`
- `path`
- `raw_path`
- `headers`
- `query_string`
- `client`
- `server`
- `scheme`
- `app`

Including `http_version` is also a good idea because downstream code
may assume it exists.

#### 2. The synthetic body must be bytes

The ASGI `http.request` message body should be `bytes`, not `str`.
`payload.model_dump_json().encode()` is the safe form.

#### 3. The synthetic scope must preserve request state needed by dependencies

This is the easiest detail to miss.

The lifespan helper resolves app-managed state from `request.state`,
specifically `request.state.lifespan_data`. If the synthetic request is
created with an empty or missing `state`, any dependency that relies on
the lifespan map can fail before routing reaches the target handler.

When constructing the synthetic scope, copy the existing request state
forward. In practice this means carrying over the parent scope's state
mapping so the internal request can still resolve lifespan-managed
objects.

#### 4. The route path must match an actual FastAPI route

`run_webhook(..., '/github/process/notification', payload)` works
because the app defines:

```python
@router.post('/process/notification')
async def process_notification(...)
```

and the router itself is mounted with the `/github` prefix. Use
`router.url_path_for(...)` or another path-producing helper that gives
you the routed path string for the internal endpoint.

#### 5. Task failures must be logged

If the created task raises an exception and nobody inspects it, the
system looks like a no-op.
[DispatchState.task_finished()][fastapi_patterns.dispatching.DispatchState.task_finished]
should always inspect and log `task.exception()`.

### Why `process_notification()` may appear to never run

When dispatch is broken, the visible symptom is usually "the endpoint
returned 204 but nothing happened." Common causes are:

- the synthetic scope is missing required HTTP fields
- the internal body is malformed
- the internal path does not match a route
- the synthetic request lost `request.state`, so a dependency failed
  before the handler was invoked
- the task raised an exception that was never logged

The most misleading version is the lifespan/state failure. In that case,
the app can accept the external webhook successfully, schedule a task,
and still never reach `process_notification()`.

### Debugging checklist

When this breaks, verify these in order:

1. Confirm the public `receive_notification()` route logs before calling
   `run_webhook`.
2. Confirm
   [DispatchState.schedule_request()][fastapi_patterns.dispatching.DispatchState.schedule_request]
   logs the task name and target path.
3. Confirm
   [DispatchState.task_finished()][fastapi_patterns.dispatching.DispatchState.task_finished]
   logs either completion or the exception traceback.
4. Inspect the synthetic scope and make sure `path`, `method='POST'`,
   `headers`, and `state` are present.
5. Confirm the synthetic `receive()` returns exactly one
   `http.request` message with a byte body and `more_body=False`.
6. Reissue the webhook against a running server and compare logs from
   the public route and the internal processing route.

## Notes for future changes

Before changing this machinery, read these files together:

- `src/fastapi_patterns/dispatching.py`
- `src/fastapi_patterns/lifespan.py`
- `src/fastapi_webhook/entrypoints.py`
- `src/fastapi_webhook/github.py`
- `src/fastapi_webhook/pagerduty.py`

Do not assume that "new task" automatically means "fresh dependency
chain." The fresh dependency chain comes from re-entering FastAPI with a
valid synthetic request, not from `asyncio.create_task()` by itself.
