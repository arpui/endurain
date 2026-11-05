"""41: every mapped way an activity actually ran over

Technical stretches only show where the ground was remarkable; the network of
paths an athlete has physically crossed is what makes the network complete -
the connective tissue a future route is built from. Each pass over a way by an
activity is one row, positioned along the way's own drawn order, checked against
the extract of the day.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "428fa94ac0ad"
down_revision: str | None = "d9f3b6c04a21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analysis_way_crossings",
        sa.Column("user_id", sa.Integer(), nullable=False, comment="Athlete who crossed it"),
        sa.Column("activity_id", sa.Integer(), nullable=False, comment="Session of the crossing"),
        sa.Column("way_id", sa.BigInteger(), nullable=False, comment="OSM way the track ran along"),
        sa.Column(
            "start_m",
            sa.Float(),
            nullable=False,
            comment="Offset along the way where the crossing starts, in drawn order",
        ),
        sa.Column("end_m", sa.Float(), nullable=False, comment="Offset along the way where it ends"),
        sa.Column("covered_m", sa.Float(), nullable=False, comment="Ground of the crossing the way accounts for"),
        sa.Column(
            "cache_date", sa.Date(), nullable=False, comment="Day of the extract this crossing was checked against"
        ),
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
        sa.ForeignKeyConstraint(["way_id"], ["analysis_osm_paths.way_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "activity_id", "way_id", "start_m"),
    )
    op.create_index("ix_analysis_way_crossings_activity", "analysis_way_crossings", ["activity_id"])
    op.create_index("ix_analysis_way_crossings_user_way", "analysis_way_crossings", ["user_id", "way_id"])
    # the session row carries which extract its whole track was covered against,
    # so a no-match activity is not re-measured every hour
    op.add_column(
        "analysis_trail_sessions",
        sa.Column(
            "coverage_cache_date",
            sa.Date(),
            nullable=True,
            comment="Extract the session's full track was matched against",
        ),
    )


def downgrade() -> None:
    op.drop_column("analysis_trail_sessions", "coverage_cache_date")
    op.drop_index("ix_analysis_way_crossings_user_way", table_name="analysis_way_crossings")
    op.drop_index("ix_analysis_way_crossings_activity", table_name="analysis_way_crossings")
    op.drop_table("analysis_way_crossings")
