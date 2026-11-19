from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field, StrictBool, StringConstraints, TypeAdapter, ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from .config import Settings
from .db.models import CampaignCreation
from .db.session import get_engine
from .keitaro import OfferBinding, fetch_offer, get_from_keitaro, request_keitaro

GOOGLE_URL = "https://google.com"


class CampaignInput(BaseModel):
    operation_id: UUID
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    country: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Za-z]{2}$")]
    offer_id: int = Field(gt=0)
    domain_id: int = Field(gt=0)
    group_id: int = Field(gt=0)
    traffic_source_id: int = Field(gt=0)
    enable_after_create: StrictBool = False


class Reference(BaseModel):
    id: int
    name: str


class CampaignOptions(BaseModel):
    domains: list[Reference]
    groups: list[Reference]
    traffic_sources: list[Reference]


class CampaignCreated(BaseModel):
    id: int
    name: str
    state: Literal["disabled", "active"]
    domain_id: int
    group_id: int
    traffic_source_id: int
    geo_stream_id: int
    offer_stream_id: int


class ResourceId(BaseModel):
    id: int = Field(gt=0)


class StoredCampaign(ResourceId):
    name: str
    state: str
    domain_id: int
    group_id: int
    traffic_source_id: int


class GeoFilter(BaseModel):
    name: str
    mode: str
    payload: list[str]


class CreatedFlow(ResourceId):
    campaign_id: int
    type: str
    state: str
    schema_name: str = Field(alias="schema")
    action_type: str
    action_payload: str | None = None
    filters: list[GeoFilter] = Field(default_factory=list)
    offers: list[OfferBinding]


references_adapter = TypeAdapter(list[Reference])
flows_adapter = TypeAdapter(list[CreatedFlow])


def load_campaign_options(settings: Settings) -> CampaignOptions:
    def load(path: str) -> list[Reference]:
        response = get_from_keitaro(path, settings, "Справочник Keitaro недоступен.")
        return references_adapter.validate_json(response.content)

    try:
        return CampaignOptions(
            domains=load("/domains"),
            groups=load("/groups?type=campaigns"),
            traffic_sources=load("/traffic_sources"),
        )
    except ValidationError as exc:
        raise HTTPException(502, "Keitaro вернул неожиданный формат справочника.") from exc


def create_campaign(data: CampaignInput, settings: Settings) -> CampaignCreated:
    operation_id = str(data.operation_id)
    alias = f"adrobot-{data.operation_id.hex}"
    payload = data.model_dump(mode="json")
    engine = get_engine(settings.database_path)

    def previous_result(operation: CampaignCreation) -> CampaignCreated:
        if operation.payload != payload:
            raise HTTPException(409, "Идентификатор операции уже использован с другими настройками.")
        if operation.result is not None:
            return CampaignCreated.model_validate(operation.result)
        raise HTTPException(409, {
            "message": f"Операция уже начата. Проверьте кампанию с alias {alias} в Keitaro. Повторное создание остановлено.",
            "alias": alias,
        })

    with Session(engine) as session:
        operation = session.get(CampaignCreation, operation_id)
        if operation is not None:
            return previous_result(operation)

    # Validation is read-only; failures here can safely be retried.
    validate_campaign(data, settings)
    with Session(engine) as session, session.begin():
        session.execute(text("BEGIN IMMEDIATE"))
        operation = session.get(CampaignCreation, operation_id)
        if operation is not None:
            return previous_result(operation)
        session.add(CampaignCreation(operation_id=operation_id, payload=payload))

    # A committed reservation prevents duplicate POSTs even after a crash or timeout.
    try:
        result = perform_creation(data, settings, alias)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        raise HTTPException(exc.status_code, {
            **detail,
            "message": f"{detail['message']} Проверьте кампанию с alias {alias}. Повторное создание этой операции остановлено.",
            "alias": alias,
        }) from exc
    with Session(engine) as session, session.begin():
        operation = session.get(CampaignCreation, operation_id)
        operation.result = result.model_dump(mode="json")
    return result


def validate_campaign(data: CampaignInput, settings: Settings) -> None:
    options = load_campaign_options(settings)
    for chosen_id, available, label in (
        (data.domain_id, options.domains, "домен"),
        (data.group_id, options.groups, "группа"),
        (data.traffic_source_id, options.traffic_sources, "источник"),
    ):
        if chosen_id not in {item.id for item in available}:
            raise HTTPException(422, f"Выбранный справочник недоступен: {label}.")

    fetch_offer(data.offer_id, settings)


def perform_creation(data: CampaignInput, settings: Settings, alias: str) -> CampaignCreated:
    response = request_keitaro(
        "POST", "/campaigns", settings, "Метод создания кампании недоступен.",
        json={
            "name": data.name,
            "alias": alias,
            "state": "disabled",
            "type": "position",
            "domain_id": data.domain_id,
            "group_id": data.group_id,
            "traffic_source_id": data.traffic_source_id,
        },
    )
    try:
        campaign_id = ResourceId.model_validate_json(response.content).id
    except ValidationError as exc:
        raise HTTPException(502, f"Keitaro не вернул ID кампании. Проверьте кампанию с alias {alias}.") from exc

    geo_filter = GeoFilter(name="country", mode="accept", payload=[data.country])
    offer = OfferBinding(offer_id=data.offer_id, share=100, state="active")
    stage = "создание GEO-потока"
    try:
        create_flow(settings, {
            "campaign_id": campaign_id,
            "name": f"Google ({data.country})",
            "position": 1,
            "type": "regular",
            "schema": "redirect",
            "action_type": "http",
            "action_payload": GOOGLE_URL,
            "filters": [geo_filter.model_dump()],
        })
        stage = "создание замыкающего потока"
        create_flow(settings, {
            "campaign_id": campaign_id,
            "name": "Offers",
            "position": 2,
            "type": "default",
            "schema": "landings",
            "action_type": "http",
            "offers": [offer.model_dump()],
        })
        stage = "проверка кампании и потоков"
        verified = verify_campaign(settings, data, campaign_id, geo_filter, offer)
        if not data.enable_after_create:
            return verified

        stage = "включение кампании"
        request_keitaro(
            "POST", f"/campaigns/{campaign_id}/enable", settings, "Кампания не найдена.",
        )
        stage = "подтверждение включения"
        enabled = StoredCampaign.model_validate_json(get_from_keitaro(
            f"/campaigns/{campaign_id}", settings, "Кампания не найдена.",
        ).content)
        if enabled.id != campaign_id or enabled.state != "active":
            raise HTTPException(502, "Keitaro не подтвердил активное состояние кампании.")
        return verified.model_copy(update={"state": "active"})
    except (HTTPException, ValidationError) as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else "Неожиданный формат ответа Keitaro."
        raise HTTPException(502, {
            "message": f"Кампания {campaign_id} создана. Этап «{stage}» не завершён. {detail}",
            "campaign_id": campaign_id,
            "stage": stage,
        }) from exc


def create_flow(settings: Settings, payload: dict) -> None:
    request_keitaro(
        "POST", "/streams", settings, "Метод создания потока недоступен.", json=payload,
    )


def verify_campaign(
    settings: Settings, data: CampaignInput, campaign_id: int, geo_filter: GeoFilter,
    offer: OfferBinding,
) -> CampaignCreated:
    campaign = StoredCampaign.model_validate_json(get_from_keitaro(
        f"/campaigns/{campaign_id}", settings, "Кампания не найдена.",
    ).content)
    if (
        campaign.id != campaign_id or campaign.name != data.name or campaign.state != "disabled"
        or campaign.domain_id != data.domain_id or campaign.group_id != data.group_id
        or campaign.traffic_source_id != data.traffic_source_id
    ):
        raise HTTPException(502, "Настройки кампании отличаются от ожидаемых.")

    flows = flows_adapter.validate_json(get_from_keitaro(
        f"/campaigns/{campaign_id}/streams", settings, "Потоки кампании не найдены.",
    ).content)
    geo = next((row for row in flows if row.type == "regular"), None)
    fallback = next((row for row in flows if row.type == "default"), None)
    if (
        len(flows) != 2 or any(row.campaign_id != campaign_id or row.state != "active" for row in flows)
        or geo is None or geo.schema_name != "redirect" or geo.action_type != "http"
        or geo.action_payload != GOOGLE_URL or geo.filters != [geo_filter] or geo.offers
        or fallback is None or fallback.schema_name != "landings"
        or fallback.filters or fallback.offers != [offer]
    ):
        raise HTTPException(502, "Настройки потоков отличаются от ожидаемых.")

    return CampaignCreated(
        id=campaign_id, name=campaign.name, state="disabled", domain_id=campaign.domain_id,
        group_id=campaign.group_id, traffic_source_id=campaign.traffic_source_id,
        geo_stream_id=geo.id, offer_stream_id=fallback.id,
    )
