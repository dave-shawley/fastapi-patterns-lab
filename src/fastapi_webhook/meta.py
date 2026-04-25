import typing as t

import fastapi
import pydantic

import fastapi_webhook

router = fastapi.APIRouter()


class ServiceStatus(pydantic.BaseModel):
    status: t.Literal['ok', 'starting']
    service_name: str = 'fastapi-webhook'
    version: str


@router.get('/status')
async def get_service_status() -> ServiceStatus:
    return ServiceStatus.model_validate(
        {'status': 'ok', 'version': fastapi_webhook.version}
    )
