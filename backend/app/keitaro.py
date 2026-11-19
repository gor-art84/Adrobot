from typing import Literal

import httpx
from fastapi import HTTPException
from pydantic import AliasChoices, BaseModel, Field, TypeAdapter, ValidationError

from .config import Settings


class OfferBinding(BaseModel):
    offer_id: int
    share: int
    state: str


class Stream(BaseModel):
    id: int
    name: str
    type: Literal["regular", "forced", "default"]
    campaign_id: int | None = None
    offers: list[OfferBinding] = Field(default_factory=list)
    routing_schema: str | None = Field(
        default=None, validation_alias=AliasChoices("routing_schema", "schema")
    )


streams_adapter = TypeAdapter(list[Stream])


def request_keitaro(
    method: str, path: str, settings: Settings, not_found: str, **kwargs
) -> httpx.Response:
    api_key = settings.keitaro_api_key.get_secret_value()
    if not api_key:
        raise HTTPException(503, "Настройте KEITARO_API_KEY на сервере.")

    url = f"{str(settings.keitaro_base_url).rstrip('/')}/admin_api/v1"
    try:
        response = httpx.request(
            method,
            f"{url}{path}",
            headers={"Api-Key": api_key, "Accept": "application/json"},
            timeout=10,
            **kwargs,
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "Keitaro не ответил вовремя.") from exc
    except httpx.RequestError as exc:
        raise HTTPException(502, "Не удалось подключиться к Keitaro.") from exc

    if response.status_code == 404:
        raise HTTPException(404, not_found)
    if response.status_code in (401, 403):
        raise HTTPException(502, "Keitaro отклонил доступ. Проверьте ключ и его права.")
    if not response.is_success:
        raise HTTPException(502, f"Keitaro вернул HTTP {response.status_code}.")

    return response


def get_from_keitaro(path: str, settings: Settings, not_found: str) -> httpx.Response:
    return request_keitaro("GET", path, settings, not_found)


def fetch_stream(stream_id: int, settings: Settings) -> Stream:
    response = get_from_keitaro(f"/streams/{stream_id}", settings, "Поток не найден.")
    try:
        stream = Stream.model_validate_json(response.content)
    except ValidationError as exc:
        raise HTTPException(502, "Keitaro вернул неожиданный формат потока.") from exc
    if "offers" not in stream.model_fields_set:
        raise HTTPException(502, "Keitaro не вернул список офферов потока.")
    return stream


def replace_offers(stream_id: int, offers: list[OfferBinding], settings: Settings) -> None:
    request_keitaro(
        "PUT", f"/streams/{stream_id}", settings, "Поток не найден.",
        json={"offers": [offer.model_dump() for offer in offers]},
    )


def fetch_streams(campaign_id: int, settings: Settings) -> list[Stream]:
    response = get_from_keitaro(
        f"/campaigns/{campaign_id}/streams", settings, "Кампания не найдена."
    )
    try:
        return streams_adapter.validate_json(response.content)
    except ValidationError as exc:
        raise HTTPException(502, "Keitaro вернул неожиданный формат данных.") from exc


class Offer(BaseModel):
    id: int
    name: str


def fetch_offer(offer_id: int, settings: Settings) -> Offer:
    response = get_from_keitaro(f"/offers/{offer_id}", settings, "Оффер не найден.")
    try:
        offer = Offer.model_validate_json(response.content)
    except ValidationError as exc:
        raise HTTPException(502, "Keitaro вернул неожиданный формат оффера.") from exc
    if offer.id != offer_id:
        raise HTTPException(502, "Keitaro вернул оффер с другим ID.")
    return offer
