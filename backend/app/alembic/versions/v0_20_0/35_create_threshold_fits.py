"""35: persist the per-session threshold fits that feed the temporal line

Revision ID: 8f4a3d07b2c6
Revises: 7d21c4a9e0b3
Create Date: 2026-11-05 00:00:00.000000

The temporal threshold (and the CP-trend envelope drawn with it) needs every
session's own quality-gated fit, and building them reads every stream the
athlete owns - a minute of work that repeated on the first request after any
restart, which the UI felt as a blank chart. The fits depend only on the
streams, so they are cached in a table: built once, dropped by the threshold
refresh and the full recompute (the same moments that expire the in-process
cache), and rebuilt on the next need.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8f4a3d07b2c6"
down_revision: str | None = "7d21c4a9e0b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analysis_threshold_fits",
        sa.Column("user_id", sa.Integer(), nullable=False, comment="Athlete the evidence belongs to"),
        sa.Column("sport", sa.String(length=32), nullable=False, comment="Sport group the fit was made for"),
        sa.Column("measure", sa.String(length=8), nullable=False, comment="power (watts) or speed (metres per second)"),
        sa.Column("activity_id", sa.Integer(), nullable=False, comment="Session the fit came from"),
        sa.Column("started_on", sa.Date(), nullable=False, comment="Day the fit supports"),
        sa.Column("value", sa.Float(), nullable=False, comment="Fitted threshold in the measure's units"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "sport", "measure", "activity_id"),
    )


def downgrade() -> None:
    op.drop_table("analysis_threshold_fits")
