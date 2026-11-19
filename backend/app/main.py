import logging
from datetime import UTC, datetime
from functools import lru_cache
from threading import Lock
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .campaigns import CampaignCreated, CampaignInput, CampaignOptions, create_campaign, load_campaign_options
from .config import Settings
from .db.models import CampaignSnapshot
from .db.repository import save_snapshot
from .db.session import get_engine
from .keitaro import Stream, fetch_active_offer, fetch_streams
from .publication import publish_draft
from .stream_editor import (
    DraftAction, EditorStream, OfferAction, change_draft, list_editor_streams,
    sync_confirmed_offers,
)

app = FastAPI(title="Adrobot")
logger = logging.getLogger(__name__)
mutation_locks = tuple(Lock() for _ in range(64))


class PushRequest(BaseModel):
    revision: int = Field(ge=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()


@app.get("/api/campaign-options")
def campaign_options(settings: Annotated[Settings, Depends(get_settings)]) -> CampaignOptions:
    return load_campaign_options(settings)


@app.post("/api/campaigns", status_code=201)
def create_campaign_route(
    data: CampaignInput,
    settings: Annotated[Settings, Depends(get_settings)],
) -> CampaignCreated:
    return create_campaign(data, settings)


@app.get("/api/campaigns/{campaign_id}/streams")
def list_streams(
    campaign_id: Annotated[int, Path(gt=0)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[Stream]:
    # An older request may finish after a newer one and must not overwrite it.
    fetched_at = datetime.now(UTC).replace(tzinfo=None)
    streams = fetch_streams(campaign_id, settings)

    try:
        with Session(get_engine(settings.database_path)) as session, session.begin():
            session.execute(text("BEGIN IMMEDIATE"))
            prior = session.get(CampaignSnapshot, campaign_id)
            previous = [Stream.model_validate(row) for row in prior.streams] if prior else []
            if save_snapshot(session, campaign_id, fetched_at, streams):
                sync_confirmed_offers(session, campaign_id, previous, streams)
    except SQLAlchemyError as exc:
        logger.error("Could not save snapshot for campaign %s: %s", campaign_id, type(exc).__name__)
        raise HTTPException(503, "Не удалось сохранить снимок кампании в SQLite.") from exc

    return streams


@app.get("/api/campaigns/{campaign_id}/drafts")
def get_drafts(
    campaign_id: Annotated[int, Path(gt=0)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[EditorStream]:
    with Session(get_engine(settings.database_path)) as session:
        return list_editor_streams(session, campaign_id)


@app.post("/api/campaigns/{campaign_id}/streams/{stream_id}/draft")
def edit_draft(
    campaign_id: Annotated[int, Path(gt=0)],
    stream_id: Annotated[int, Path(gt=0)],
    action: DraftAction,
    settings: Annotated[Settings, Depends(get_settings)],
) -> EditorStream:
    name = None
    if isinstance(action, OfferAction) and action.action == "add":
        name = fetch_active_offer(action.offer_id, settings).name

    lock = mutation_locks[campaign_id % len(mutation_locks)]
    with lock, Session(get_engine(settings.database_path)) as session, session.begin():
        # Serialize the revision check and write with other SQLite writers.
        session.execute(text("BEGIN IMMEDIATE"))
        return change_draft(session, campaign_id, stream_id, action, name)


@app.post("/api/campaigns/{campaign_id}/streams/{stream_id}/push")
def push_stream(
    campaign_id: Annotated[int, Path(gt=0)],
    stream_id: Annotated[int, Path(gt=0)],
    request: PushRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> EditorStream:
    lock = mutation_locks[campaign_id % len(mutation_locks)]
    try:
        with lock:
            return publish_draft(
                get_engine(settings.database_path), settings, campaign_id, stream_id, request.revision
            )
    except SQLAlchemyError as exc:
        logger.error("Could not confirm publication for stream %s: %s", stream_id, type(exc).__name__)
        raise HTTPException(503, "Не удалось сохранить подтверждение в SQLite. Черновик сохранён.") from exc
