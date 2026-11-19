"""Create stream drafts."""

import sqlalchemy as sa
from alembic import op

revision = "0002_stream_drafts"
down_revision = "0001_campaign_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stream_drafts",
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("stream_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("offers", sa.JSON(none_as_null=True), nullable=True),
        sa.PrimaryKeyConstraint("campaign_id", "stream_id"),
    )


def downgrade() -> None:
    op.drop_table("stream_drafts")
