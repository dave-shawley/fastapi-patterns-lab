# Composable FastAPI Lifespans

::: fastapi_patterns.lifespan

## Additional type machinery

There are a number of type aliases used in this module that you will see referenced
in signatures. You shouldn't need to use them directly or worry about them too much,
They are used to make the type system work for you and a part of what makes the small
dependency injection helpers work so well.

* `_ClassAsyncContextManager` is a [typing.Protocol][] that describes context managers that the [Lifespan][fastapi_patterns.lifespan.Lifespan] accepts.
* `FunctionLifespanHook` describes the functions that [Lifespan][fastapi_patterns.lifespan.Lifespan] accepts.
* `ClassLifespanHook` describes the callable returning a context manager that [Lifespan][fastapi_patterns.lifespan.Lifespan] accepts.
* `LifespanHook` is the union of `FunctionLifespanHook` and `ClassLifespanHook`.
* `TypedLifespanHook` is a generic version of `LifespanHook` that participates in type inference in [get_state][fastapi_patterns.lifespan.Lifespan.get_state]

You shouldn't need to use these directly, but if you want to dig into them for some
reason, at least you have some explanation of what they are there for.
