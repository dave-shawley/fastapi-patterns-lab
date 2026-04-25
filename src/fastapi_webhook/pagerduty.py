import datetime
import getpass
import logging
import typing as t

import fastapi
import httpx
import keyring
import pydantic
import pydantic_settings
import rich.prompt
import typer
import yarl

from fastapi_patterns import utilities
from fastapi_patterns.dispatching import DispatchTaskRunner  # noqa: TC001

router = fastapi.APIRouter(prefix='/pagerduty')
cli = typer.Typer()


class PagerDutySettings(pydantic_settings.BaseSettings):
    model_config = {'env_prefix': 'PAGERDUTY_'}
    base_url: yarl.URL = yarl.URL('https://api.pagerduty.com/')
    api_token: pydantic.SecretStr


class Resource(pydantic.BaseModel):
    id: str
    type: str
    self: pydantic.AnyHttpUrl | None
    html_url: pydantic.AnyHttpUrl | None


class Event(pydantic.BaseModel):
    id: str
    occurred_at: datetime.datetime
    agent: Resource | None


class PingEvent(pydantic.BaseModel):
    event_type: t.Literal['pagey.ping']


class Incident(pydantic.BaseModel):
    number: int
    title: str
    created_at: datetime.datetime
    status: str
    incident_key: str | None
    service: Resource | None
    escalation_policy: Resource | None
    urgency: str
    id: str
    type: t.Literal['incident']
    self: pydantic.AnyHttpUrl
    html_url: pydantic.AnyHttpUrl


class IncidentEvent(pydantic.BaseModel):
    event_type: t.Literal[
        'incident.acknowledged',
        'incident.escalated',
        'incident.reassigned',
        'incident.resolved',
        'incident.triggered',
    ]
    data: Incident


class CEFDetails(pydantic.BaseModel):
    dedup_key: str
    description: str
    message: str
    source_component: str | None  # PD Settings> Component
    event_class: str | None  # PD Settings> Class
    service_group: str | None  # PD Settings> Group


class AlertBody(pydantic.BaseModel):
    type: t.Literal['alert_body']
    cef_details: CEFDetails


class Alert(pydantic.BaseModel):
    alert_key: str
    id: str
    html_url: pydantic.AnyHttpUrl
    type: t.Literal['alert']
    incident: Resource
    body: AlertBody


class AlertsResponse(pydantic.BaseModel):
    alerts: list[Alert]


class PDEventPayload(pydantic.BaseModel):
    event: t.Annotated[
        PingEvent | IncidentEvent, pydantic.Field(discriminator='event_type')
    ]


class PagerDutyClient(httpx.AsyncClient):
    def __init__(self) -> None:
        settings = utilities.settings_from_environment(PagerDutySettings)
        auth_token = settings.api_token.get_secret_value()
        self.logger = logging.getLogger(__package__).getChild(
            'PagerDutyClient'
        )
        super().__init__(
            base_url=str(settings.base_url),
            headers={'authorization': f'Token token={auth_token}'},
        )


@router.post('/notification', status_code=204)
async def receive_notification(
    payload: PDEventPayload, run_webhook: DispatchTaskRunner
) -> None:
    logger = logging.getLogger(__name__).getChild('receive_notification')
    match payload.event:
        case IncidentEvent():
            logger.info(
                'queueing pagerduty notification %s on %s',
                payload.event.event_type,
                utilities.get_task_name(),
            )
            run_webhook(
                f'pagerduty-{payload.event.data.id}',
                router.url_path_for('process_notification'),
                payload,
            )
        case PingEvent() | _:
            logger.info('ignoring event type %s', payload.event.event_type)
            return


@router.post('/process/notification', include_in_schema=False)
async def process_notification(payload: PDEventPayload) -> None:
    logger = logging.getLogger(__name__).getChild('process_notification')
    incident_id: str
    match payload.event:
        case IncidentEvent():
            logger.info(
                'processing pagerduty notification %s on %s',
                payload.event.event_type,
                utilities.get_task_name(),
            )
            incident_id = payload.event.data.id
        case PingEvent() | _:
            logger.info('ignoring event type %s', payload.event.event_type)
            return

    logger.info(
        'retrieving alerts for incident %s on %s',
        incident_id,
        utilities.get_task_name(),
    )
    async with PagerDutyClient() as client:
        rsp = await client.get(f'/incidents/{incident_id}/alerts')
        if rsp.is_success:
            alerts = AlertsResponse.model_validate(rsp.json())
            rich.print(alerts.alerts[0])
        else:
            logger.error(
                'failed to retrieve alerts for incident %s: %r',
                incident_id,
                rsp.text,
            )


@cli.command()
def install_pagerduty_webhook(
    endpoint: str,
    *,
    service: t.Annotated[str | None, typer.Option('--service')] = None,
) -> None:
    api_key: str | None
    try:
        settings = utilities.settings_from_environment(PagerDutySettings)
        api_key = settings.api_token.get_secret_value()
    except KeyError:
        api_key = keyring.get_password('pagerduty.com', getpass.getuser())
        if api_key is None:
            api_key = rich.prompt.Prompt.ask(
                'Please enter your Pagerduty API key', password=True
            )

    if not api_key:
        typer.echo('A Pagerduty API key is required.')
        raise typer.Exit()

    callback_url = yarl.URL(endpoint)
    if callback_url.scheme != 'https':
        typer.echo('Pagerduty webhook only supports HTTPS')
        raise typer.Exit()
    if callback_url.path == '/':
        callback_url = callback_url.with_path('/pagerduty/notification')

    payload = CreateWebhookSubscription.model_validate(
        {
            'description': 'Incident webhook agent',
            'events': ['incident.triggered'],
            'delivery_method': {'url': callback_url},
            'filter': WebhookAccountFilter(),
        }
    )
    if service:
        payload.filter = WebhookServiceFilter.model_validate({'id': service})

    with httpx.Client(base_url='https://api.pagerduty.com/') as client:
        rsp = client.post(
            '/webhook_subscriptions',
            headers={
                'authorization': f'Token token={api_key}',
                'accept': 'application/json',
            },
            json={'webhook_subscription': payload.model_dump(mode='python')},
        )
        if rsp.is_success:
            subscription_info = rsp.json()['webhook_subscription']
            rich.print(
                f'Created webhook subscription {subscription_info["id"]}'
            )
        else:
            rich.print(rsp)
            rich.print(rsp.json())


class WebhookDeliveryMethod(pydantic.BaseModel):
    type: t.Literal['http_delivery_method'] = 'http_delivery_method'
    url: yarl.URL


class WebhookAccountFilter(pydantic.BaseModel):
    type: t.Literal['account_reference'] = 'account_reference'


class WebhookServiceFilter(pydantic.BaseModel):
    type: t.Literal['service_reference'] = 'service_reference'
    id: str


class CreateWebhookSubscription(pydantic.BaseModel):
    type: t.Literal['webhook_subscription'] = 'webhook_subscription'
    delivery_method: WebhookDeliveryMethod
    description: str
    events: list[str]
    filter: t.Annotated[
        WebhookAccountFilter | WebhookServiceFilter,
        pydantic.Field(discriminator='type'),
    ]
