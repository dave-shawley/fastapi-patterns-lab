# Exposing Dependencies With Annotated Aliases

Using [typing.Annotated][] with [fastapi.Depends][] is already part of
FastAPI's normal dependency story, but in this repository the pattern
has a specific job: make one dependency helper available everywhere
through one stable type alias. The main goal is not cosmetic. It is to
keep the same dependency function in use consistently across handlers,
sub-dependencies, and feature modules.

Reducing signature clutter is still useful, but it is the secondary
benefit.

## Why this exists

FastAPI makes it easy to write this inline:

```python
commons: t.Annotated[dict, fastapi.Depends(common_parameters)]
```

That is fine for one or two call sites. It becomes harder to manage
once the same dependency starts carrying real application policy:

- signature validation for one class of webhooks
- access to a lifespan-managed object
- injection of a callable helper with specific behavior
- authentication or tenant-resolution rules that must not drift

At that point, repeating `Annotated[..., Depends(...)]` inline makes it
too easy for call sites to diverge. One route may call a different
helper. Another may copy the return type but forget metadata. A third
may wrap the original dependency in a slightly different function.

Creating one exported alias gives the dependency a stable name. Callers
import the contract they want instead of re-spelling the machinery.

## See it in action

This repository already uses the pattern in a few places:

- [fastapi_patterns.lifespan.LifespanMap][] exposes the request-time
  handle for lifespan-managed state
- [fastapi_patterns.dispatching.DispatchTaskRunner][] exposes the
  callable that redispatches a validated payload to an internal route
- `ValidPayloadSignature` in `fastapi_webhook.github` exposes one
  specific signature-validation dependency for GitHub webhooks

These aliases are not interchangeable. Each one names a specific
dependency contract.

## High-level model

The moving parts are small:

- write one dependency helper function that returns the value callers
  should receive
- define one type alias with `Annotated[ValueType, Depends(helper)]`
- export that alias from the feature module that owns the helper
- use the alias in handlers instead of repeating `Depends(...)` inline

The important design choice is the API boundary: route signatures depend
on the alias, not on the helper implementation details.

## How to use it

The normal pattern has four steps.

### 1. Define the dependency helper

Keep the helper focused on one semantic job.

```python title="security.py"
import fastapi


async def _current_account(
    authorization: str = fastapi.Header(),
) -> Account:
    ...
```

The helper can do as much work as needed: parse headers, validate a
signature, resolve a database-backed object, or construct a callable to
return to the handler.

### 2. Export one alias for that dependency

Put the alias next to the helper so the intended pairing is obvious.

```python title="security.py"
import typing as t

import fastapi


CurrentAccount = t.Annotated[Account, fastapi.Depends(_current_account)]
```

This is still ordinary Python typing. FastAPI reads the
`Annotated[..., Depends(...)]` metadata the same way it would if the
annotation were written inline.

### 3. Use the alias in handlers

```python title="routes.py"
from .security import CurrentAccount


@router.get('/accounts/me')
async def read_current_account(account: CurrentAccount) -> AccountResponse:
    ...
```

Now the handler advertises the dependency contract by name. The route
does not need to know how that value is produced.

### 4. Prefer the alias everywhere that contract is needed

This is the rule that makes the pattern valuable.

If a module already exports `CurrentAccount`, do not keep writing one of
these at new call sites:

```python
account: t.Annotated[Account, fastapi.Depends(_current_account)]
```

or:

```python
account: Account = fastapi.Depends(_current_account)
```

Those spellings work, but they weaken the benefit of having a named
dependency contract in the first place.

## When to reach for this helper

Use an annotated alias when all of these are true:

- the same dependency helper should be reused in multiple places
- consistency matters more than letting each call site choose its own
  spelling
- the helper returns a value with a meaningful static type
- you want route signatures to depend on a named contract

If a dependency is only used once, an inline `Annotated[..., Depends(...)]`
parameter is usually simpler.

## Implementation details

### Why consistency is the main value

FastAPI's own documentation shows the alias pattern primarily as a way
to remove duplication from signatures. That is true, and it remains a
good reason to use it in larger code bases. The repository uses the same
mechanism for a stricter purpose: one importable symbol should stand for
one dependency decision.

That matters when the dependency is part of application behavior, not
just syntax. `DispatchTaskRunner` does not merely "return a callable."
It returns the callable that builds a synthetic ASGI request for the
internal dispatching pattern. `LifespanMap` does not merely "return a
request helper." It returns the route-facing handle for lifespan state.

The alias makes that choice explicit.

### What the alias preserves

This pattern preserves both halves of the dependency declaration:

- FastAPI still sees the `Depends(...)` metadata and resolves the
  dependency normally
- type checkers still see the injected value type and can preserve
  completions and diagnostics

That is why the pattern works equally well for:

- ordinary value dependencies
- callable helpers such as `DispatchTaskRunner`
- infrastructure handles such as `LifespanMap`

### Where the active pieces live

If you need concrete examples in this repository, read these files
together:

- `src/fastapi_webhook/github.py`
- `src/fastapi_patterns/dispatching.py`
- `src/fastapi_patterns/lifespan.py`
- `docs/patterns/lifespan.md`
- `docs/patterns/dispatching.md`

### Required invariants

There are a few rules that keep the pattern coherent.

#### 1. One alias should mean one dependency contract

Do not use the same alias name for "mostly similar" dependencies.

If one route needs a validated GitHub webhook signature and another
needs a raw header value, those should not share an alias just because
both happen to involve the same header.

#### 2. The alias should live next to the helper that defines it

Keeping the helper and alias in the same feature module makes it easier
to preserve the intended pairing and easier for callers to import the
right contract.

#### 3. Call sites should import the alias instead of re-spelling it

If the project exports a named alias but new routes continue to write
`Depends(...)` inline, the project no longer has one obvious dependency
contract. It has a convention that can drift.

#### 4. The return type should be specific

Avoid aliases like:

```python
Thing = t.Annotated[object, fastapi.Depends(_get_thing)]
```

when a more precise type is available. The point of the alias is not
only reuse. It is reuse with the correct type visible at the handler
boundary.

## Notes for future changes

Before changing examples or guidance around this pattern, read these
files together:

- `src/fastapi_webhook/github.py`
- `src/fastapi_patterns/dispatching.py`
- `src/fastapi_patterns/lifespan.py`
- `docs/patterns/lifespan.md`
- `docs/patterns/dispatching.md`

Do not describe this pattern as only "removing clutter from function
signatures." In this repository its primary value is enforcing
consistent use of the same dependency helper.
