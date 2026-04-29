"""FastAPI lifespan composition with type-safe dependency injection.

!!! warning "Problem"

    FastAPI accepts only one lifespan callable, but applications need
    multiple independent resources (database pools, Redis connections)
    with separate setup/teardown lifecycles.

!!! success "Solution"

    The [Lifespan][.Lifespan] class composes multiple async state providers
    into a single lifespan while preserving type information through
    dependency injection.

Use the following pattern to define state that is readily available in any
request handler.

```python title="postgres.py"
from fastapi_patterns import lifespan

@contextlib.asynccontextmanager
async def postgres_lifespan() -> abc.AsyncIterator[PoolType]: # (1)!
    async with psycopg_pool.AsyncConnectionPool(...) as pool:
        yield pool

async def _inject_pool(
    context: lifespan.LifespanMap
) -> abc.AsyncIterator[PoolType]:
    pool = context.get_state(postgres_lifespan) # (2)!
    async with pool.connection() as conn:
        yield conn

PostgresPool = t.Annotated[ # (3)!
    PoolType, fastapi.Depends(_inject_pool)
]
```

1. Define lifespan hooks as async context managers returning your state
2. Define dependency injection functions using [get_state][.Lifespan.get_state]
3. Create type aliases with [typing.Annotated][] and [fastapi.Depends][]:
   these will be used in route handlers to access the state

```python title="app.py"
import fastapi
from fastapi_patterns import lifespan

from my_package import postgres

app = fastapi.FastAPI(
    lifespan=lifespan.Lifespan(postgres.postgres_lifespan), # (1)!
)

@app.get('/')
async def handler(*, pool: postgres.PostgresPool) -> None: # (2)!
    ...
```

1. Create a [Lifespan][.Lifespan] instance combining all hooks in
   your application
2. Use type aliases from your provider in route handlers to access
   the state

"""

import contextlib
import http
import types
import typing as t
from collections import abc

import fastapi


class _ClassAsyncContextManager[T](t.Protocol):
    async def __aenter__(self) -> T: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_traceback: types.TracebackType | None,
    ) -> object: ...


type FunctionLifespanHook[T] = abc.Callable[
    [fastapi.FastAPI], contextlib.AbstractAsyncContextManager[T]
]
type ClassLifespanHook[T] = abc.Callable[
    [fastapi.FastAPI], _ClassAsyncContextManager[T]
]
type LifespanHook = (
    FunctionLifespanHook[object | None] | ClassLifespanHook[object | None]
)
type TypedLifespanHook[T] = FunctionLifespanHook[T] | ClassLifespanHook[T]


@t.overload
def _as_async_context_manager[T](
    cm: contextlib.AbstractAsyncContextManager[T],
) -> contextlib.AbstractAsyncContextManager[T]: ...


@t.overload
def _as_async_context_manager[T](
    cm: _ClassAsyncContextManager[T],
) -> contextlib.AbstractAsyncContextManager[T]: ...


def _as_async_context_manager[T](
    cm: object,
) -> contextlib.AbstractAsyncContextManager[T]:
    return t.cast('contextlib.AbstractAsyncContextManager[T]', cm)


class Lifespan(dict[LifespanHook, object | None]):
    """Compose multiple lifespan hooks into a single FastAPI lifespan.

    Manages multiple independent async context managers (lifespan hooks)
    and provides type-safe access to their yielded resources through
    dependency injection. Hooks are deduplicated (same hook only runs
    once) and cleaned up in LIFO order.

    Example:
        ```python

        @contextlib.asynccontextmanager
        async def postgres_lifespan() -> abc.AsyncIterator[PoolType]:
            async with psycopg_pool.AsyncConnectionPool(...) as pool:
                yield pool

        app = fastapi.FastAPI(
            lifespan=Lifespan(postgres_lifespan, redis_lifespan)
        )
        ```

    See Also:
        * [get_state][.get_state]: Retrieve resources from hooks with
            type preservation
        * [LifespanMap][..LifespanMap]: Type alias for dependency injection
    """

    def __init__(self, *hooks: LifespanHook) -> None:
        """Initialize Lifespan with the given hooks.

        Args:
            *hooks: Variable number of lifespan hooks to combine.
                Hooks are entered in the order provided and exited
                in LIFO order. Duplicate hooks are deduplicated
                automatically.
        """
        super().__init__()
        self._hooks: tuple[LifespanHook, ...] = hooks

    def get_state[T](self, hook: TypedLifespanHook[T]) -> T:
        """Retrieve the resource yielded by a specific hook.

        This is a generic method that preserves type information.
        If the hook yields a resource of type `T`, this method
        returns `T`. Use this method to create dependency injection
        functions for use with [fastapi.Depends][].

        Args:
            hook: The lifespan hook whose resource to retrieve. Must
                have been passed to the initializer.

        Returns:
            T: The resource yielded by the hook, with type preserved.

        Raises:
            fastapi.HTTPException: 500 error if the hook was not
                registered with this `Lifespan` instance.

        Example:
            ```python

            def _inject_pool(context: LifespanMap) -> PoolType:
                # Type of pool is PoolType (not object)
                pool = context.get_state(postgres_lifespan)
                return pool

            Pool = t.Annotated[PoolType, fastapi.Depends(_inject_pool)]
            ```
        """
        try:
            return t.cast('T', self[hook])
        except KeyError:
            raise fastapi.HTTPException(
                http.HTTPStatus.INTERNAL_SERVER_ERROR,
                detail=f'Unmet lifespan dependency hook {hook!r}',
            ) from None

    def __call__(
        self, app: fastapi.FastAPI
    ) -> contextlib.AbstractAsyncContextManager[dict[str, Lifespan]]:
        """Make Lifespan callable as a FastAPI lifespan function.

        This method is called automatically by FastAPI during application
        startup. It enters all registered hooks, stores their yielded
        resources, and ensures proper cleanup on shutdown.

        Args:
            app: The FastAPI application instance.

        Returns:
            An async context manager that yields a dictionary
                containing the Lifespan instance under the key
                'lifespan_data'.

        Note:
            - Hooks are entered in the order provided to `__init__`
            - Duplicate hooks are detected and only executed once
            - Resources are cleaned up in LIFO order (last-in-first-out)
            - Uses AsyncExitStack to ensure proper cleanup even if hooks
              raise exceptions
        """

        @contextlib.asynccontextmanager
        async def cm() -> abc.AsyncIterator[dict[str, Lifespan]]:
            async with contextlib.AsyncExitStack() as stack:
                for hook in self._hooks:
                    if hook not in self:
                        self[hook] = await stack.enter_async_context(
                            _as_async_context_manager(hook(app))
                        )
                yield {'lifespan_data': self}

        return cm()


def get_lifespan(request: fastapi.Request) -> Lifespan:
    """Extract the Lifespan instance from the request state.

    This is a FastAPI dependency function that retrieves the Lifespan
    instance from the request state.

    !!! warning
        You should be using [LifespanMap][..LifespanMap] instead!

    Args:
        request: The current request object.

    Returns:
        Lifespan: The Lifespan instance that was set up during
            application startup.

    Raises:
        fastapi.HTTPException: 500 error if the lifespan was not
            initialized (missing lifespan parameter in FastAPI()
            constructor) or if request.state.lifespan_data is not
            accessible.

    """
    lifespan_data = t.cast(
        'object', getattr(request.state, 'lifespan_data', None)
    )
    if isinstance(lifespan_data, Lifespan):
        return lifespan_data
    raise fastapi.HTTPException(
        http.HTTPStatus.INTERNAL_SERVER_ERROR, detail='Lifespan not available'
    )


type LifespanMap = t.Annotated[Lifespan, fastapi.Depends(get_lifespan)]
"""Dependency injection for Lifespan instance.

Mention this type in a parameter list to inject the [Lifespan][..Lifespan]
instance anywhere that FastAPI's dependency injection is available.

"""
