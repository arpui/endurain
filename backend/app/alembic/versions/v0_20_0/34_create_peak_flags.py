"""34: record sessions held out of the duration-intensity curve

Revision ID: 7d21c4a9e0b3
Revises: 6c9f2a48d1b7
Create Date: 2026-11-05 00:00:00.000000

The curve is a maximum over every session's samples, so one corrupt stream -
a sensor runaway reading a kilowatt at walking speed - would otherwise define
an athlete's record forever and pull the derived thresholds with it. The
builder now refuses such sessions and records one row per refused session so
the athlete can see what the curve held out and why. Refusals are replaced on
every refresh: repair the stream and the session re-enters the curve.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7d21c4a9e0b3"
down_revision: str | None = "6c9f2a48d1b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analysis_peak_flags",
        sa.Column("user_id", sa.Integer(), nullable=False, comment="Athlete this flag belongs to"),
        sa.Column("sport", sa.String(length=32), nullable=False, comment="Sport group the refused peak was fitted for"),
        sa.Column("activity_id", sa.Integer(), nullable=False, comment="Session held out of the curve"),
        sa.Column(
            "started_on",
            sa.Date(),
            nullable=False,
            comment="Day of the session, kept so the notice survives the activity",
        ),
        sa.Column(
            "duration_s",
            sa.Integer(),
            nullable=False,
            comment="Duration of the strongest violation, in seconds",
        ),
        sa.Column("value_w", sa.Float(), nullable=False, comment="The refused best average, in watts"),
        sa.Column("session_np_w", sa.Float(), nullable=False, comment="The session's own normalized power, in watts"),
        sa.Column("ratio", sa.Float(), nullable=False, comment="Refused value over the session's normalized power"),
        sa.Column("cap", sa.Float(), nullable=False, comment="The tier ratio the session exceeded"),
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
        sa.PrimaryKeyConstraint("user_id", "sport", "activity_id"),
    )


def downgrade() -> None:
    op.drop_table("analysis_peak_flags")
