"""Store the publication baseline and confirmed editor metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0003_draft_publication"
down_revision = "0002_stream_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stream_drafts", sa.Column("baseline", sa.JSON(none_as_null=True)))
    op.add_column("stream_drafts", sa.Column("published_offers", sa.JSON(none_as_null=True)))


def downgrade() -> None:
    op.drop_column("stream_drafts", "published_offers")
    op.drop_column("stream_drafts", "baseline")
