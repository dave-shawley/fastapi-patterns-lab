# Dispatching Webhooks Internally

This project accepts an external webhook request, validates and decodes
it once, then re-dispatches the decoded payload through the FastAPI
application as a new internal request. That second request is meant to
run in its own task with a fresh request scope and a fresh dependency
resolution pass.

This document explains the moving parts in that flow and the invariants
that must hold for it to work.

## Why this exists

The public webhook endpoint has two jobs:

1. Authenticate and normalize the incoming request.
2. Hand off the actual processing to a separate task.

The second step matters because the processor path should not inherit
the original request object or its dependency instances. The goal is to
let the processing endpoint execute as if it were handling a new HTTP
request created inside the same application. This takes advantage of
FastAPI's features such as dependency injection and authorization.

Doing this as a separate task is important since the caller often
expects webhooks to respond quickly. By separating the processing
into a background task, the public endpoint can return a response
immediately, freeing up resources and improving overall system
performance. For example, GitHub requires that webhook responses
be sent within 10 seconds.

## High-level flow

GitHub and PagerDuty notifications both use this pattern. The concrete
flow below uses GitHub as the example:

1. `POST /github/notification` enters
   [github.py](/Users/daveshawley/Source/python/fastapi-webhook/src/fastapi_webhook/github.py).
2. `receive_notification()` verifies the signature, reads the request
   body, and validates it into a `PingPayload` or `PushPayload`.
3. `receive_notification()` calls `run_webhook(...)`, which is injected
   as `dispatching.DispatchTaskRunner`.
4. `DispatchTaskRunner` builds a synthetic ASGI `http` scope and a
   synthetic `receive()` callable that returns the already-decoded
   payload as JSON.
5. The dispatch state schedules `app(scope, receive, send)` in a new
   `asyncio` task.
6. FastAPI routes that synthetic request to
   `POST /github/process/notification`.
7. `process_notification()` executes with a new request scope and a new
   dependency chain.

The public endpoint does not call `process_notification()` directly. It
re-enters the ASGI application so routing, dependency injection, and request
parsing all happen again.

## The modules and their roles

### `entrypoints.py`

[entrypoints.py](/Users/daveshawley/Source/python/fastapi-webhook/src/fastapi_webhook/entrypoints.py)
constructs the app and wires in two pieces that make dispatching
possible:

- `fastapi.FastAPI(lifespan=lifespan.Lifespan(dispatching.DispatchState))`

The lifespan registration is what makes the dispatch state available to
request dependencies. Without it, the internal dispatcher has nowhere to
register tasks.

### `lifespan.py`

[lifespan.py](/Users/daveshawley/Source/python/fastapi-webhook/src/fastapi_patterns/lifespan.py)
provides the `Lifespan` object that stores resources created at app
startup in `request.state.lifespan_data`.

The important part for dispatching is `_get_lifespan()`. Dependencies
that need lifespan-managed state resolve it from `request.state`. That
means any synthetic request must preserve the state FastAPI expects.

### `dispatching.py`

[dispatching.py](/Users/daveshawley/Source/python/fastapi-webhook/src/fastapi_patterns/dispatching.py)
contains the internal dispatch machinery.

`DispatchState` provides the app-lifetime task registry used by the
dispatcher.

`DispatchState.schedule_request()` starts a new `asyncio` task that calls
`self.app(scope, receive, _blackhole_send)`. That line is the real
dispatch. It does not invoke a handler directly; it re-enters the full
application stack.

`_get_dispatch_task_runner()` is the active dependency behind
`DispatchTaskRunner`. It closes over the current request, then returns a
callable that:

- accepts a task name, route path, and pydantic payload
- builds a synthetic `http` ASGI scope
- builds a synthetic `receive()` that returns one `http.request` message
- asks the lifespan-managed `DispatchState` to schedule the task

### `github.py`

[github.py](/Users/daveshawley/Source/python/fastapi-webhook/src/fastapi_webhook/github.py)
is the concrete example of the pattern.

`receive_notification()` is responsible for:

- validating the GitHub signature
- selecting the expected payload model from the `X-GitHub-Event` header
- decoding the body once
- calling `run_webhook(task_name, path, payload)`

`process_notification()` is the actual processing endpoint. It receives
the payload as a normal FastAPI body parameter, which means the internal
request must look like a real POST with JSON content.

## What “fresh dependency chain” means here

FastAPI resolves dependencies per request. By creating a new ASGI scope
and routing it back through the app, the processor gets:

- a new `Request` instance
- a new dependency-resolution pass
- a new body parsing pass
- a separate `asyncio` task

It does not create a second application. App-level state, lifespan
resources, router configuration, and middleware stack are shared.

## Required invariants

If any of these are broken, the dispatch may silently fail before the
processing handler runs.

### 1. The synthetic scope must be a valid HTTP scope

At a minimum it needs the fields Starlette and FastAPI expect for an HTTP
request, including:

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

Including `http_version` is also a good idea because downstream code may
assume it exists.

### 2. The synthetic body must be bytes

The ASGI `http.request` message body should be `bytes`, not `str`.
`payload.model_dump_json().encode()` is the safe form.

### 3. The synthetic scope must preserve request state needed by dependencies

This is the easiest detail to miss.

The lifespan helper resolves app-managed state from `request.state`,
specifically `request.state.lifespan_data`. If the synthetic request is
created with an empty or missing `state`, any dependency that relies on
the lifespan map can fail before routing reaches the target handler.

When constructing the synthetic scope, copy the existing request state
forward. In practice this means carrying over the parent scope's state
mapping so the internal request can still resolve lifespan-managed
objects.

### 4. The route path must match an actual FastAPI route

`run_webhook(..., '/github/process/notification', payload)` works
because `github.py` defines:

```python
@router.post('/process/notification')
async def process_notification(...)
```

and the router itself is mounted with the `/github` prefix. You should
use `router.url_path_for()` to construct the path.

### 5. Task failures must be logged

If the created task raises an exception and nobody inspects it, the
system looks like a no-op. `DispatchState._task_finished()` should
always log
exceptions from `task.exception()`.

## Why `process_notification()` may appear to never run

When dispatch is broken, the visible symptom is usually “the endpoint
returned 204 but nothing happened.” Common causes are:

- the synthetic scope is missing required HTTP fields
- the internal body is malformed
- the internal path does not match a route
- the synthetic request lost `request.state`, so a dependency failed
  before the handler was invoked
- the task raised an exception that was never logged

The most misleading version is the lifespan/state failure. In that case,
the app can accept the external webhook successfully, schedule a task,
and still never reach `process_notification()`.

## Debugging checklist

When this breaks, verify these in order:

1. Confirm `receive_notification()` logs before calling `run_webhook`.
2. Confirm `DispatchState.schedule_request()` logs the task name and
   target
   path.
3. Confirm `DispatchState._task_finished()` logs either completion or the
   exception traceback.
4. Inspect the synthetic scope and make sure `path`,
   `method='POST'`, `headers`, and `state` are present.
5. Confirm the synthetic `receive()` returns exactly one
   `http.request` message with a byte body and `more_body=False`.
6. Reissue the webhook against a running server and compare logs from
   the public route and the internal processing route.

## Extending the pattern

To add another webhook source that uses this mechanism:

1. Create a public route that authenticates and decodes the external
   payload.
2. Create a processing route that accepts the normalized payload as a
   standard FastAPI body parameter.
3. Inject `dispatching.DispatchTaskRunner` into the public route.
4. Call `run_webhook(task_name, internal_path, payload)`.
5. Keep any lifespan-dependent dependencies compatible with a synthetic
   request by preserving `request.state`.

Prefer normal `http` synthetic scopes over the older custom `webhook`
scope type unless there is a specific need to bypass FastAPI routing and
dependency injection.

## Notes for future AI assistants

Before changing this machinery, read these files together:

- [entrypoints.py](/Users/daveshawley/Source/python/fastapi-webhook/src/fastapi_webhook/entrypoints.py)
- [lifespan.py](/Users/daveshawley/Source/python/fastapi-webhook/src/fastapi_patterns/lifespan.py)
- [dispatching.py](/Users/daveshawley/Source/python/fastapi-webhook/src/fastapi_patterns/dispatching.py)
- [github.py](/Users/daveshawley/Source/python/fastapi-webhook/src/fastapi_webhook/github.py)

Do not assume that “new task” automatically means “fresh dependency
chain.” The fresh dependency chain comes from re-entering FastAPI with a
valid synthetic request, not from `asyncio.create_task()` by itself.
