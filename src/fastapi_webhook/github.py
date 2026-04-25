import hmac
import logging
import typing as t

import fastapi
import pydantic
import pydantic_settings
import yarl

from fastapi_patterns import utilities
from fastapi_patterns.dispatching import DispatchTaskRunner  # noqa: TC001

router = fastapi.APIRouter(prefix='/github')


class GitHubSettings(pydantic_settings.BaseSettings):
    model_config = {'env_prefix': 'GITHUB_'}
    api_url: yarl.URL = yarl.URL('https://api.github.com/')
    hook_secrets: dict[int, str] = {}


class User(pydantic.BaseModel):
    type: t.Literal['User']
    login: str
    id: int
    node_id: str
    html_url: pydantic.AnyHttpUrl
    url: pydantic.AnyHttpUrl


class Repository(pydantic.BaseModel):
    id: int
    node_id: str
    name: str
    full_name: str
    html_url: pydantic.AnyHttpUrl
    url: pydantic.AnyHttpUrl
    archived: bool
    fork: bool
    owner: User
    default_branch: str


class PingPayload(pydantic.BaseModel):
    hook_id: int


class PushPayload(pydantic.BaseModel):
    repository: Repository
    sender: User


async def _validated_signature(
    hook_id: t.Annotated[int, fastapi.Header(alias='X-GitHub-Hook-ID')],
    signature: t.Annotated[str, fastapi.Header(alias='X-Hub-Signature-256')],
    *,
    request: fastapi.Request,
) -> str:
    logger = logging.getLogger(__name__).getChild('validated_checksum')
    settings = utilities.settings_from_environment(GitHubSettings)
    if secret := settings.hook_secrets.get(hook_id):
        body = await request.body()
        hasher = hmac.HMAC(secret.encode(), body, 'sha256')
        calculated = 'sha256=' + hasher.hexdigest()
        if signature != calculated:
            logger.warning(
                'signature mismatch: calculated=%r received=%r',
                calculated,
                signature,
            )
            raise fastapi.HTTPException(400)
    return signature


ValidPayloadSignature = t.Annotated[str, fastapi.Depends(_validated_signature)]


@router.post('/notification', status_code=204)
async def receive_notification(
    request: fastapi.Request,
    *,
    event: t.Annotated[str, fastapi.Header(alias='X-GitHub-Event')],
    hook_id: t.Annotated[int, fastapi.Header(alias='X-GitHub-Hook-ID')],
    run_webhook: DispatchTaskRunner,
    _signature: ValidPayloadSignature,
) -> None:
    logger = logging.getLogger(__name__).getChild('receive_notification')

    payload: PingPayload | PushPayload
    match event:
        case 'ping':
            payload = PingPayload.model_validate_json(await request.body())
            if payload.hook_id != hook_id:
                logger.warning(
                    'hook_id mismatch: expected=%r received=%r',
                    hook_id,
                    payload.hook_id,
                )
                raise fastapi.HTTPException(400)
        case 'push':
            payload = PushPayload.model_validate_json(await request.body())
        case _:
            logger.info('ignoring event type %s', event)
            return

    logger.info(
        'processing github notification %s on %s',
        event,
        utilities.get_task_name(),
    )
    run_webhook(
        f'github-{hook_id}',
        router.url_path_for('process_notification'),
        payload,
    )


@router.post('/process/notification', include_in_schema=False)
async def process_notification(payload: PushPayload | PingPayload) -> None:
    logger = logging.getLogger(__name__).getChild('process_notification')
    logger.info('processing %r on %s', payload, utilities.get_task_name())
