from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db.models import CampaignSnapshot, StreamDraft
from .keitaro import OfferBinding, Stream
from .weights import InvalidWeights, Weight, distribute_weights


class DraftOffer(BaseModel):
    offer_id: int
    share: int
    state: str
    pinned: bool = False
    removed: bool = False
    name: str | None = None


class EditorStream(BaseModel):
    id: int
    name: str
    editable: bool
    dirty: bool
    conflict: bool
    revision: int
    offers: list[DraftOffer]


class OfferAction(BaseModel):
    action: Literal["add", "remove", "restore", "pin"]
    offer_id: int = Field(gt=0)
    revision: int = Field(ge=0)


class CancelAction(BaseModel):
    action: Literal["cancel"]
    revision: int = Field(ge=0)


DraftAction = Annotated[OfferAction | CancelAction, Field(discriminator="action")]


def offer_signature(offers: list[OfferBinding]) -> list[dict]:
    return [offer.model_dump() for offer in sorted(
        offers, key=lambda row: (row.offer_id, row.state, row.share)
    )]


def campaign_streams(session: Session, campaign_id: int) -> list[Stream]:
    snapshot = session.get(CampaignSnapshot, campaign_id)
    if snapshot is None:
        raise HTTPException(404, "Сначала загрузите потоки кампании.")
    return [Stream.model_validate(stream) for stream in snapshot.streams]


def editor_stream(stream: Stream, draft: StreamDraft | None) -> EditorStream:
    editable = bool(stream.offers) or stream.routing_schema in {"landings", "offers"}
    saved_offers = draft.offers if draft is not None else None
    confirmed_offers = draft.published_offers if draft is not None else None
    displayed_offers = saved_offers if saved_offers is not None else confirmed_offers
    offers = (
        [DraftOffer.model_validate(offer) for offer in displayed_offers]
        if displayed_offers is not None
        else [DraftOffer.model_validate(offer.model_dump()) for offer in stream.offers]
    )
    return EditorStream(
        id=stream.id,
        name=stream.name,
        editable=editable,
        dirty=saved_offers is not None,
        conflict=saved_offers is not None and draft is not None
        and draft.baseline != offer_signature(stream.offers),
        revision=draft.revision if draft else 0,
        offers=offers,
    )


def sync_confirmed_offers(
    session: Session, campaign_id: int, previous: list[Stream], incoming: list[Stream]
) -> None:
    previous_streams = {stream.id: stream for stream in previous}
    drafts = {
        draft.stream_id: draft
        for draft in session.scalars(
            select(StreamDraft).where(StreamDraft.campaign_id == campaign_id)
        )
    }
    for stream in incoming:
        prior = previous_streams.get(stream.id)
        draft = drafts.get(stream.id)
        published = draft.published_offers if draft is not None else None
        saved = [DraftOffer.model_validate(entry) for entry in published or []]
        current_signature = offer_signature(stream.offers)
        previous_offers = prior.offers if prior else []
        saved_is_current = published is None or offer_signature([
            OfferBinding(offer_id=row.offer_id, share=row.share, state=row.state)
            for row in saved if not row.removed
        ]) == current_signature
        if (prior is None or offer_signature(previous_offers) == current_signature) and saved_is_current:
            continue

        if draft is None:
            draft = StreamDraft(campaign_id=campaign_id, stream_id=stream.id, revision=0)
            session.add(draft)
        if draft.offers is None:
            draft.revision += 1

        known = {
            offer.offer_id: DraftOffer.model_validate(offer.model_dump())
            for offer in previous_offers
        }
        for offer in saved:
            known[offer.offer_id] = offer

        confirmed = []
        for binding in stream.offers:
            old = known.pop(binding.offer_id, None)
            confirmed.append(DraftOffer(
                **binding.model_dump(),
                name=old.name if old else None,
                pinned=bool(
                    old and old.pinned and not old.removed
                    and old.share == binding.share and binding.state == "active"
                ),
            ))
        confirmed.extend(
            offer.model_copy(update={"removed": True, "share": 0, "pinned": False})
            for offer in known.values()
        )
        draft.published_offers = [offer.model_dump(mode="json") for offer in confirmed]


def list_editor_streams(session: Session, campaign_id: int) -> list[EditorStream]:
    drafts = {
        draft.stream_id: draft
        for draft in session.scalars(
            select(StreamDraft).where(StreamDraft.campaign_id == campaign_id)
        )
    }
    return [
        editor_stream(stream, drafts.get(stream.id))
        for stream in campaign_streams(session, campaign_id)
    ]


def change_draft(
    session: Session,
    campaign_id: int,
    stream_id: int,
    action: DraftAction,
    offer_name: str | None,
) -> EditorStream:
    stream = next((row for row in campaign_streams(session, campaign_id) if row.id == stream_id), None)
    if stream is None:
        raise HTTPException(404, "Поток отсутствует в снимке кампании.")
    draft = session.get(StreamDraft, (campaign_id, stream_id))
    current = editor_stream(stream, draft)
    if current.revision != action.revision:
        raise HTTPException(409, "Черновик изменился. Загрузите потоки снова.")
    if not current.editable and not isinstance(action, CancelAction):
        raise HTTPException(422, "Этот поток не поддерживает офферы.")

    if draft is None:
        draft = StreamDraft(campaign_id=campaign_id, stream_id=stream_id, revision=0)
        session.add(draft)

    draft.revision += 1
    if isinstance(action, CancelAction):
        draft.offers = None
    else:
        update_offers(current.offers, action, offer_name)
        if not current.dirty:
            draft.baseline = offer_signature(stream.offers)
        draft.offers = [offer.model_dump(mode="json") for offer in current.offers]
    return editor_stream(stream, draft)


def update_offers(offers: list[DraftOffer], action: OfferAction, name: str | None) -> None:
    row = next((offer for offer in offers if offer.offer_id == action.offer_id), None)
    if action.action == "add":
        if row is not None:
            raise HTTPException(409, "Оффер уже в списке. Для снятого оффера используйте Bring back.")
        offers.append(DraftOffer(offer_id=action.offer_id, share=0, state="active", name=name))
    elif row is None:
        raise HTTPException(404, "Оффер отсутствует в потоке.")
    elif action.action == "restore" and row.removed:
        row.removed = False
    elif action.action == "remove" and not row.removed:
        row.removed = True
        row.pinned = False
    elif action.action == "pin" and not row.removed and row.state == "active":
        row.pinned = not row.pinned
        return
    else:
        raise HTTPException(422, "Действие недоступно для этого оффера.")

    try:
        shares = distribute_weights([
            Weight(offer.offer_id, offer.share, offer.pinned)
            for offer in offers if not offer.removed and offer.state == "active"
        ])
    except InvalidWeights as exc:
        raise HTTPException(422, str(exc)) from exc

    for offer in offers:
        offer.share = shares.get(offer.offer_id, 0)
