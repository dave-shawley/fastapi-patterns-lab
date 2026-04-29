# FastAPI Patterns Lab

This repository is a working lab for exploring FastAPI and ASGI
patterns in real code.

It started as an investigation into webhook processing, but the goal is
broader than "a webhook app." The repository is meant to collect
patterns that are easier to understand when they are:

- implemented in runnable code
- explained in article-style documents
- exercised in a small example application

Some of those patterns may eventually settle into a reusable library.
That is why the repository now has two layers: a reusable kernel in
`fastapi_patterns`, and an example application that consumes it.

## What This Repository Is

This is not a polished product or a turnkey framework.

It is a place to:

- explore FastAPI design patterns
- document why those patterns exist
- keep example code and explanatory writing close together
- separate reusable ideas from app-specific examples

The repository is intentionally allowed to evolve as new patterns become
worth documenting.

## Repository Layout

- `src/fastapi_patterns/`
  The reusable kernel. This is the package shipped in the wheel today.
- `src/fastapi_webhook/`
  A small example application that demonstrates the patterns in
  practice. It stays in the repository as runnable reference code and a
  demo target for local development.
- `docs/`
  Article-style notes about individual patterns. These are intended to
  become GitHub Pages content over time.

## Current Patterns

The repository currently focuses on a few related ideas:

- composable FastAPI lifespan management with typed state lookup
- re-dispatching validated webhook payloads back through the ASGI app as
  fresh internal requests
- small support utilities that make settings loading and task-aware
  logging less repetitive
- concrete webhook examples using GitHub and PagerDuty

## Package Boundary

The package boundary is intentional.

`fastapi_patterns` is where reusable code belongs. If something in this
repository feels like a generally useful technique rather than an
application detail, it should probably migrate there.

`fastapi_webhook` is the example application layer. It is useful for:

- showing how the patterns fit together in a real app
- providing concrete routes and payload models
- giving the repository a fast local demo path

At the moment, the built wheel only includes `fastapi_patterns`.

## Running The Example App

This project currently targets Python `3.14`. I use the
[just](https://just.systems/) utility to manage development tasks. If
you haven't used it before, you can install pre-built binaries from the
[just releases page](https://github.com/casey/just/releases). See the
[just docs](https://just.systems/docs/installation) for more details.
You can create a virtual environment, install dependencies, and run the
example app with one simple command:

```bash
just serve
```

Useful endpoints:

- `GET /status`
- `POST /github/notification`
- `POST /pagerduty/notification`

The public webhook endpoints validate and normalize incoming payloads,
then hand work off to internal processing routes using the dispatching
pattern described in [Dispatching webhooks internally](patterns/dispatching.md).

## Development

Useful commands:

```bash
just format
just lint
```

`just serve` remains in the repository on purpose even though the
example app is not part of the published wheel. The repo should stay
easy to run while the patterns are still being explored.

## Direction

The likely long-term shape is:

- a repository of documented FastAPI patterns
- a published `fastapi_patterns` package containing the reusable parts
- example applications that prove the patterns out before they are
  treated as library surface area

Until that settles, this repository should be read as a lab notebook
with working code, not as a finished framework.
