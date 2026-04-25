"""Shared helper utilities used by the reusable patterns."""

import asyncio
import typing as t

import pydantic_settings


def settings_from_environment[T: pydantic_settings.BaseSettings](
    model_cls: type[T],
) -> T:
    return model_cls()


def get_task_name(task: asyncio.Task[t.Any] | None = None) -> str:
    if task is None:
        task = asyncio.current_task()
    return 'unnamed task' if task is None else task.get_name()
