from datetime import datetime

from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from ..keitaro import Stream
from .models import CampaignSnapshot


def save_snapshot(
    session: Session, campaign_id: int, fetched_at: datetime, streams: list[Stream]
) -> bool:
    statement = insert(CampaignSnapshot).values(
        campaign_id=campaign_id,
        fetched_at=fetched_at,
        streams=[stream.model_dump(mode="json") for stream in streams],
    )
    saved_id = session.scalar(
        statement.on_conflict_do_update(
            index_elements=[CampaignSnapshot.campaign_id],
            set_={
                "fetched_at": statement.excluded.fetched_at,
                "streams": statement.excluded.streams,
            },
            where=statement.excluded.fetched_at >= CampaignSnapshot.fetched_at,
        ).returning(CampaignSnapshot.campaign_id)
    )
    return saved_id is not None
