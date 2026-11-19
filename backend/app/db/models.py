from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CampaignSnapshot(Base):
    __tablename__ = "campaign_snapshots"

    campaign_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    streams: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)


class StreamDraft(Base):
    __tablename__ = "stream_drafts"

    campaign_id: Mapped[int] = mapped_column(primary_key=True)
    stream_id: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(nullable=False)
    offers: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON(none_as_null=True))
    baseline: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON(none_as_null=True))
    published_offers: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON(none_as_null=True))
