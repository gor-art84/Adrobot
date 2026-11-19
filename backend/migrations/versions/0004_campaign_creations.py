"""Persist campaign creation operations before calling Keitaro."""

import sqlalchemy as sa
from alembic import op

revision = "0004_campaign_creations"
down_revision = "0003_draft_publication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaign_creations",
        sa.Column("operation_id", sa.String(), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(none_as_null=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("campaign_creations")
