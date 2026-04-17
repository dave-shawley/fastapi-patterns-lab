import pydantic_settings


def settings_from_environment[T: pydantic_settings.BaseSettings](
    model_cls: type[T],
) -> T:
    return model_cls()
