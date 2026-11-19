from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from .config import Settings
from .db.models import CampaignSnapshot, StreamDraft
from .keitaro import OfferBinding, Stream, fetch_stream, replace_offers
from .stream_editor import DraftOffer, EditorStream, editor_stream, offer_signature


def publish_draft(
    engine: Engine, settings: Settings, campaign_id: int, stream_id: int, revision: int,
) -> EditorStream:
    with Session(engine) as session:
        draft = session.get(StreamDraft, (campaign_id, stream_id))
        if draft is None or draft.offers is None:
            raise HTTPException(409, "Нет черновика для публикации.")
        if draft.revision != revision:
            raise HTTPException(409, "Черновик изменился. Загрузите потоки снова.")
        if draft.baseline is None:
            raise HTTPException(409, "У черновика нет исходного снимка. Отмените его и внесите изменения снова.")
        baseline = draft.baseline
        rows = [DraftOffer.model_validate(row) for row in draft.offers]

    desired = [
        OfferBinding(offer_id=row.offer_id, share=row.share, state=row.state)
        for row in rows if not row.removed
    ]
    expected = offer_signature(desired)
    remote = read_target(settings, campaign_id, stream_id)
    actual = offer_signature(remote.offers)
    if actual != baseline and actual != expected:
        raise HTTPException(409, "Офферы изменились в Keitaro. Черновик сохранён.")

    if actual != expected:
        write_error = None
        try:
            replace_offers(stream_id, desired, settings)
        except HTTPException as exc:
            write_error = exc

        try:
            remote = read_target(settings, campaign_id, stream_id)
        except HTTPException as exc:
            raise HTTPException(502, "Не удалось подтвердить запись в Keitaro. Черновик сохранён.") from exc
        if offer_signature(remote.offers) != expected:
            if write_error is not None:
                raise write_error
            raise HTTPException(502, "Keitaro вернул другой состав или доли. Черновик сохранён.")

    with Session(engine) as session, session.begin():
        session.execute(text("BEGIN IMMEDIATE"))
        draft = session.get(StreamDraft, (campaign_id, stream_id))
        snapshot = session.get(CampaignSnapshot, campaign_id)
        if draft is None or draft.revision != revision or snapshot is None:
            raise HTTPException(409, "Локальное состояние изменилось. Загрузите потоки снова.")
        snapshot.streams = [
            remote.model_dump(mode="json") if stream["id"] == stream_id else stream
            for stream in snapshot.streams
        ]
        snapshot.fetched_at = datetime.now(UTC).replace(tzinfo=None)
        draft.baseline = expected
        draft.published_offers = [row.model_dump(mode="json") for row in rows]
        draft.offers = None
        draft.revision += 1
        return editor_stream(remote, draft)


def read_target(settings: Settings, campaign_id: int, stream_id: int) -> Stream:
    remote = fetch_stream(stream_id, settings)
    if remote.id != stream_id or remote.campaign_id != campaign_id:
        raise HTTPException(409, "Поток не принадлежит указанной кампании.")
    return remote
