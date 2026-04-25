import asyncio
import types
import typing as t

import pydantic_settings


def settings_from_environment[T: pydantic_settings.BaseSettings](
    model_cls: type[T],
) -> T:
    return model_cls()


def unwrap[T](value: T | None) -> T:
    if value is None:
        raise ValueError('Value is None')
    return value


def unwrap_as[T](typ: type[T], value: object | None) -> T:
    origin = t.get_origin(typ) or typ
    if origin is not None and origin not in (t.Union, types.UnionType):
        typ = origin
    if value is None:
        raise ValueError('Value is None')
    if isinstance(value, typ):
        return value
    raise ValueError(f'Value is a {type(value)}, expected {typ}')


def get_task_name(task: asyncio.Task[t.Any] | None = None) -> str:
    if task is None:
        task = asyncio.current_task()
    return 'unnamed task' if task is None else task.get_name()
