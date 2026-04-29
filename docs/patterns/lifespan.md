# Composable FastAPI Lifespans

The [fastapi_patterns.lifespan][] module lets each feature define its
own app-lifetime resource while still giving FastAPI exactly one
`lifespan=` callable. It also keeps request-time access typed, so route
handlers can ask for the resource they need without reaching into
`request.state` directly.

## Why this exists

FastAPI accepts exactly one `lifespan=` callable when the application is
constructed. Real applications usually need more than one startup and
shutdown concern:

- a database pool
- a Redis client
- a background-task coordinator
- an application-scoped state object

Without a helper, those resources tend to get folded into one large
lifespan function with manual setup, manual teardown ordering, and
untyped lookups from `app.state` or `request.state`.

The [fastapi_patterns.lifespan.Lifespan][] class keeps those concerns
independent. Each feature owns its own hook. The application composes
the hooks once. Request-time code uses a small dependency helper to
recover the resource for one hook with the correct static type.

## See it in action

In this repository, the `fastapi_webhook` example application uses
lifespan composition to manage the internal webhook dispatcher described
in [Dispatching Webhooks Internally](dispatching.md).

## High-level model

The moving parts are small:

- `LifespanHook` describes a callable that accepts `fastapi.FastAPI`
  and returns an async context manager. In practice that means either
  a [contextlib.asynccontextmanager][]-decorated function or a class
  constructor whose instances implement `__aenter__()` and `__aexit__()`.
- The application passes one
  [Lifespan][fastapi_patterns.lifespan.Lifespan] instance to
  `fastapi.FastAPI(lifespan=...)`.
- During startup, `Lifespan.__call__()` enters each hook, stores the
  yielded value under the hook object, and yields
  `{'lifespan_data': self}` to FastAPI.
- During request handling, FastAPI dependency injection injects a
  [LifespanMap][fastapi_patterns.lifespan.LifespanMap] instance into the
  dependency helper.
- [get_state(hook)][fastapi_patterns.lifespan.Lifespan.get_state] looks
  up the value for one hook and preserves the hook's static type.
- During shutdown, the hooks exit in reverse order.

The important design choice is the lookup key: resources are stored by
hook identity, not by string name.

## How to use it

The normal pattern has four steps. Keep the hook and the dependency
helper in the same feature module so the app wiring and the request-time
lookup both use the same hook object.

### 1. Define a hook

You can use either a [contextlib.asynccontextmanager][] decorator or a
class whose instances implement the async context-manager protocol.

```python title="postgres.py (function style)"
import contextlib
from collections import abc

import fastapi
import psycopg.pool


@contextlib.asynccontextmanager
async def postgres_lifespan(
    _app: fastapi.FastAPI,
) -> abc.AsyncIterator[psycopg.pool.AsyncConnectionPool]:
    async with psycopg.pool.AsyncConnectionPool(...) as pool:
        yield pool
```

```python title="postgres.py (class style)"
import fastapi
import psycopg.pool


class State:
    def __init__(self, app: fastapi.FastAPI) -> None:
        self.app = app
        self.pool = psycopg.pool.AsyncConnectionPool(...)

    async def __aenter__(self) -> State:
        return self

    async def __aexit__(self, exc_type, exc_value, exc_traceback) -> None:
        ...
```

### 2. Compose the hooks into the app lifespan

```python title="app.py"
import fastapi

from fastapi_patterns import lifespan

from . import postgres

app = fastapi.FastAPI(
    lifespan=lifespan.Lifespan(
        postgres.postgres_lifespan,
    )
)
```

If the same hook is passed more than once, `Lifespan` only enters it
the first time.

### 3. Write a dependency helper that asks for `LifespanMap`

```python title="postgres.py (function style)"
import typing as t

import fastapi
import psycopg.pool

from fastapi_patterns import lifespan


def _get_pool(
    lifespan_map: lifespan.LifespanMap,
) -> psycopg.pool.AsyncConnectionPool:
    return lifespan_map.get_state(postgres_lifespan)


Pool = t.Annotated[psycopg.pool.AsyncConnectionPool, fastapi.Depends(_get_pool)]
```

```python title="postgres.py (class style)"
import typing as t

import fastapi
import psycopg.pool

from fastapi_patterns import lifespan


def _get_pool(
    lifespan_map: lifespan.LifespanMap,
) -> psycopg.pool.AsyncConnectionPool:
    return lifespan_map.get_state(State).pool


Pool = t.Annotated[psycopg.pool.AsyncConnectionPool, fastapi.Depends(_get_pool)]
```

### 4. Use the typed alias in handlers

```python title="app.py"
from . import postgres


@app.get('/items')
async def list_items(pool: postgres.Pool) -> None:
    ...
```

That keeps route signatures compact while preserving the actual resource
type at the handler boundary.

## When to reach for this helper

Use `Lifespan` when all of these are true:

- the resource should live for the whole application lifetime
- setup and teardown belong together as one async context manager
- request-time dependencies need access to the resulting resource
- you want the lookup site to stay typed

If a value is purely request-scoped, ordinary FastAPI dependencies are a
better fit.

## Implementation details

### Why the typing works

The key API is:

```python
def get_state[T](self, hook: TypedLifespanHook[T]) -> T:
    ...
```

That means the type checker can infer the return type from the hook you
pass in:

- if `postgres_lifespan()` yields `AsyncConnectionPool`,
  `get_state(postgres_lifespan)` is typed as `AsyncConnectionPool`
- if `dispatching.DispatchState(app)` yields `DispatchState`,
  `get_state(dispatching.DispatchState)` is typed as `DispatchState`

That same inference works across both supported hook styles. The type
parameter comes from the value yielded by the function-style hook or
from the value returned by `__aenter__()` for the class-style hook.

This avoids string-key lookups and avoids forcing every caller to write
its own `cast(...)`.

### Exception behavior

Within one startup and shutdown cycle, `Lifespan` behaves like a normal
`AsyncExitStack`-managed resource group:

- if a later hook fails during startup, earlier hooks are still cleaned
  up in reverse order
- if a dependency asks for a hook that was never registered,
  `get_state()` raises `HTTP 500` with a specific unmet-dependency
  message
- if request handling runs without `request.state.lifespan_data`,
  [get_lifespan()][fastapi_patterns.lifespan.get_lifespan] raises
  `HTTP 500` with `detail='Lifespan not available'`

The two [fastapi.HTTPException][] cases matter because they fail in a
way FastAPI understands. Callers get a normal internal-server-error
response instead of a leaked [KeyError][] or [AttributeError][].

### Required invariants

There are a few rules that must hold for this pattern to work.

#### 1. Registration and lookup must use the same hook object

This is the most important rule.

These two pair correctly:

- `Lifespan(dispatching.DispatchState)` with
  `get_state(dispatching.DispatchState)`
- `Lifespan(postgres_lifespan)` with `get_state(postgres_lifespan)`

These do not pair correctly:

- `Lifespan(dispatching.DispatchState)` with
  `get_state(postgres_lifespan)`
- `Lifespan(postgres_lifespan)` with `get_state(other_wrapper)`

The map key is the callable or class object itself, not the type it
returns and not its name.

#### 2. The FastAPI app must actually use `Lifespan(...)`

`LifespanMap` depends on `request.state.lifespan_data`. If the app is
constructed without `fastapi.FastAPI(lifespan=...)`,
[get_lifespan()][fastapi_patterns.lifespan.get_lifespan] will raise
`HTTP 500`.

#### 3. Synthetic requests must preserve request state when they need lifespan data

This matters for the webhook dispatcher. If code creates a synthetic
ASGI request and drops the state FastAPI expects, dependencies that rely
on `LifespanMap` will fail before the target handler runs. See
[Dispatching Webhooks Internally](dispatching.md) for a description of
that pattern.

#### 4. Hooks that only perform side effects may yield `None`

The type definitions allow hooks to yield `T | None`. That means a hook
can exist only to bracket setup and teardown work. In that case, do not
expect `get_state()` to provide a meaningful resource value.

## Notes for future changes

Before changing this machinery, read these files together:

- `src/fastapi_patterns/lifespan.py`
- `src/fastapi_webhook/entrypoints.py`
- `src/fastapi_patterns/dispatching.py`
- `docs/patterns/dispatching.md`

Do not treat `Lifespan` as a string-keyed registry. Its main value is
that hook identity carries both the lookup key and the static type.
