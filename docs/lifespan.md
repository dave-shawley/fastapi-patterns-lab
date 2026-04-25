# Composable FastAPI Lifespans

This project uses [lifespan.py](/Users/daveshawley/Source/python/fastapi-webhook/src/fastapi_webhook/lifespan.py)
to compose app-lifetime resources into one FastAPI lifespan while still
letting request-time dependencies retrieve those resources with useful
types. Hooks can be written either as simple `@asynccontextmanager`
functions or as classes whose instances implement the async
context-manager protocol.

This document explains why that helper exists, what it is used for in
this repository, and the rules for using it correctly.

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

`Lifespan` exists to keep those concerns independent.

Each feature can define its own lifespan hook, either as a plain
function that returns an async context manager or as a class that
constructs one. The application can compose those hooks into one FastAPI
lifespan. Request-time code can then retrieve a specific resource by
referring to the hook that created it.

## What it is used for here

In this repository, the immediate user is the webhook processor state.
[entrypoints.py](/Users/daveshawley/Source/python/fastapi-webhook/src/fastapi_webhook/entrypoints.py)
constructs the app with `fastapi.FastAPI(lifespan=lifespan.Lifespan(...))`.
That registration makes lifespan-managed state available to normal
dependencies and to the synthetic internal requests described in
[dispatching.md](/Users/daveshawley/Source/python/fastapi-webhook/docs/dispatching.md).

The resource itself is not exposed directly on `request.state`.
Instead, `Lifespan.__call__()` yields one mapping:

```python
{'lifespan_data': self}
```

FastAPI places that mapping onto `request.state`, so dependencies can
later resolve `request.state.lifespan_data` and ask for a specific
resource with `get_state()`.

## High-level model

The moving parts are small:

- `LifespanHook` is a callable that accepts `fastapi.FastAPI` and
  returns an async context manager. In practice that means either an
  `@asynccontextmanager` function or a class constructor whose instances
  implement `__aenter__()` and `__aexit__()`.
- `Lifespan(*hooks)` stores those hooks and makes itself callable as the
  single FastAPI lifespan function.
- During startup, `Lifespan.__call__()` enters each hook with an
  `AsyncExitStack`, stores the yielded resource under the hook object,
  and yields `{'lifespan_data': self}` to FastAPI.
- During request handling, `LifespanMap` injects that stored
  `Lifespan` instance as a normal dependency.
- `get_state(hook)` looks up the value for one hook and preserves the
  hook's static type.
- During shutdown, `AsyncExitStack` exits the hooks in reverse order.

The important design choice is the lookup key: resources are stored by
hook identity, not by string name.

## Why the typing works

The key API is:

```python
def get_state[T](self, hook: TypedLifespanHook[T]) -> T:
    ...
```

That means the type checker can infer the return type from the hook you
pass in:

- if `postgres_lifespan()` yields `AsyncConnectionPool`,
  `get_state(postgres_lifespan)` is typed as `AsyncConnectionPool`
- if `processor.State(app)` yields `State`,
  `get_state(processor.State)` is typed as `State`

That same inference works across both supported hook styles. The type
parameter comes from the value yielded by the function-style hook or
from the value returned by `__aenter__()` for the class-style hook.

This avoids string-key lookups and avoids forcing every caller to write
its own `cast(...)`.

## Exception behavior

Within one startup and shutdown cycle, `Lifespan` behaves like a normal
`AsyncExitStack`-managed resource group:

- if a later hook fails during startup, earlier hooks are still cleaned
  up in reverse order
- if a dependency asks for a hook that was never registered,
  `get_state()` raises `HTTP 500` with a specific unmet-dependency
  message
- if request handling runs without `request.state.lifespan_data`,
  `_get_lifespan()` raises `HTTP 500` with `detail='Lifespan not available'`

The two `HTTPException` cases matter because they fail in a way FastAPI
understands. Callers get a normal internal-server-error response instead
of a leaked `KeyError` or `AttributeError`.

## How to use it

The normal pattern has four steps.

### 1. Define a hook

You can use either an `@asynccontextmanager` function or a class whose
instances implement the async context-manager protocol.

Function-style hook:

```python
import contextlib
from collections import abc

import fastapi


@contextlib.asynccontextmanager
async def postgres_lifespan(
    app: fastapi.FastAPI,
) -> abc.AsyncIterator[Pool]:
    async with AsyncConnectionPool(...) as pool:
        yield pool
```

Class-style hook:

```python
class State:
    def __init__(self, app: fastapi.FastAPI) -> None:
        self.app = app

    async def __aenter__(self) -> State:
        return self

    async def __aexit__(self, exc_type, exc_value, exc_traceback) -> None:
        ...
```

The class form is what
[processor.py](/Users/daveshawley/Source/python/fastapi-webhook/src/fastapi_webhook/processor.py)
uses for its application-scoped task registry.

### 2. Compose the hooks into the app lifespan

```python
from fastapi_webhook import lifespan

app = fastapi.FastAPI(
    lifespan=lifespan.Lifespan(processor.State, postgres_lifespan)
)
```

If the same hook is passed more than once, `Lifespan` only enters it
once.

### 3. Write a dependency that asks for `LifespanMap`

```python
def _get_pool(lifespan_map: lifespan.LifespanMap) -> Pool:
    return lifespan_map.get_state(postgres_lifespan)
```

Or, for the class-based example:

```python
def _get_state(lifespan_map: lifespan.LifespanMap) -> processor.State:
    return lifespan_map.get_state(processor.State)
```

### 4. Expose a typed dependency alias when that improves readability

```python
import typing as t

PoolDep = t.Annotated[Pool, fastapi.Depends(_get_pool)]


@app.get('/items')
async def list_items(pool: PoolDep) -> None:
    ...
```

That keeps route signatures compact while preserving the actual resource
type at the handler boundary.

## Required invariants

There are a few rules that must hold for this pattern to work.

### 1. Registration and lookup must use the same hook object

This is the most important rule.

These two pair correctly:

- `Lifespan(processor.State)` with `get_state(processor.State)`
- `Lifespan(postgres_lifespan)` with `get_state(postgres_lifespan)`

These do not pair correctly:

- `Lifespan(processor.State)` with
  `get_state(postgres_lifespan)`
- `Lifespan(postgres_lifespan)` with `get_state(other_wrapper)`

The map key is the callable or class object itself, not the type it
returns and not its name.

### 2. The FastAPI app must actually use `Lifespan(...)`

`LifespanMap` depends on `request.state.lifespan_data`. If the app is
constructed without `fastapi.FastAPI(lifespan=...)`, `_get_lifespan()`
will raise `HTTP 500`.

### 3. Synthetic requests must preserve request state when they need lifespan data

This matters for the webhook dispatcher. If code creates a synthetic
ASGI request and drops the state FastAPI expects, dependencies that rely
on `LifespanMap` will fail before the target handler runs. See
[dispatching.md](/Users/daveshawley/Source/python/fastapi-webhook/docs/dispatching.md)
for the details.

### 4. Hooks that only perform side effects may yield `None`

The type definitions allow hooks to yield `T | None`. That means a hook
can exist only to bracket setup and teardown work. In that case, do not
expect `get_state()` to provide a meaningful resource value.

## When to reach for this helper

Use `Lifespan` when all of these are true:

- a resource should live for the whole application lifetime
- setup and teardown belong together as one async context manager
- request-time dependencies need access to the resulting resource
- you want the lookup site to stay typed

If a value is purely request-scoped, ordinary FastAPI dependencies are a
better fit.

## Notes for future changes

Before changing this machinery, read these files together:

- [lifespan.py](/Users/daveshawley/Source/python/fastapi-webhook/src/fastapi_webhook/lifespan.py)
- [entrypoints.py](/Users/daveshawley/Source/python/fastapi-webhook/src/fastapi_webhook/entrypoints.py)
- [processor.py](/Users/daveshawley/Source/python/fastapi-webhook/src/fastapi_webhook/processor.py)
- [dispatching.md](/Users/daveshawley/Source/python/fastapi-webhook/docs/dispatching.md)

Do not treat `Lifespan` as a string-keyed registry. Its main value is
that hook identity carries both the lookup key and the static type.
