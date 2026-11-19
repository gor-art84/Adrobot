"""Create campaign snapshots.

Revision ID: 0001_campaign_snapshots
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_campaign_snapshots"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaign_snapshots",
        sa.Column("campaign_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("streams", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("campaign_id"),
    )


def downgrade() -> None:
    op.drop_table("campaign_snapshots")
