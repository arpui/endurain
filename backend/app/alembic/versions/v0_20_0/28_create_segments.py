"""28: create segments and segment_efforts

Revision ID: 9b1c4d7e2a5f
Revises: d54bdd779392
Create Date: 2026-10-29 00:00:00.000000

The segment library is global: one stored definition is matched against every
user's activities. ``geometry`` keeps the polyline as ``[[lat, lng], ...]`` in
one canonical order (index 0 is the start of a ``forward`` effort) and the four
bbox columns let the matcher reject impossible pairs before any distance work.
Efforts are the matcher's answer per (segment, activity, direction, pass); they
cascade with either side, and re-matching an activity replaces its rows whole.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9b1c4d7e2a5f"
down_revision: str | None = "d54bdd779392"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "segments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False, comment="Segment display name"),
        sa.Column(
            "source",
            sa.String(length=20),
            server_default="strava",
            nullable=False,
            comment="Definition origin: strava or manual",
        ),
        sa.Column(
            "source_id",
            sa.String(length=64),
            nullable=True,
            comment="External identifier within the source (e.g. Strava segment id)",
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            nullable=True,
            comment="ID of the user that created or imported the segment",
        ),
        sa.Column(
            "distance_m",
            sa.Float(),
            nullable=False,
            comment="Segment length in meters along the stored geometry order",
        ),
        sa.Column(
            "elevation_gain_m",
            sa.Float(),
            nullable=True,
            comment="Positive elevation gain in meters, when the source provides it",
        ),
        sa.Column("min_lat", sa.Float(), nullable=False, comment="Bounding box south edge"),
        sa.Column("min_lng", sa.Float(), nullable=False, comment="Bounding box west edge"),
        sa.Column("max_lat", sa.Float(), nullable=False, comment="Bounding box north edge"),
        sa.Column("max_lng", sa.Float(), nullable=False, comment="Bounding box east edge"),
        sa.Column(
            "geometry",
            sa.JSON(),
            nullable=False,
            comment="Ordered polyline as [[lat, lng], ...]; index 0 is the canonical start",
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
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "source_id", name="uq_segments_source_source_id"),
        sa.CheckConstraint("source IN ('strava', 'manual')", name="ck_segments_source"),
    )
    op.create_index(op.f("ix_segments_id"), "segments", ["id"], unique=False)
    op.create_index(op.f("ix_segments_created_by"), "segments", ["created_by"], unique=False)

    op.create_table(
        "segment_efforts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("segment_id", sa.Integer(), nullable=False, comment="ID of the segment traversed"),
        sa.Column("activity_id", sa.Integer(), nullable=False, comment="ID of the activity that traversed the segment"),
        sa.Column(
            "direction",
            sa.String(length=10),
            nullable=False,
            comment="forward (counts as an effort) or reverse (informational only)",
        ),
        sa.Column(
            "pass_number",
            sa.Integer(),
            server_default="1",
            nullable=False,
            comment="1-based pass index within (segment, activity, direction)",
        ),
        sa.Column("elapsed_seconds", sa.Integer(), nullable=False, comment="Effort duration in seconds"),
        sa.Column("avg_speed_mps", sa.Float(), nullable=True, comment="Segment distance over elapsed time in m/s"),
        sa.Column(
            "coverage",
            sa.Float(),
            nullable=True,
            comment="Fraction of distinct segment points touched by the pass (audit)",
        ),
        sa.Column(
            "time_start",
            sa.DateTime(),
            nullable=False,
            comment="Wall-clock time of the first effort waypoint (naive, as the stream stores it)",
        ),
        sa.Column(
            "time_end",
            sa.DateTime(),
            nullable=False,
            comment="Wall-clock time of the last effort waypoint (naive, as the stream stores it)",
        ),
        sa.Column(
            "stream_index_start",
            sa.Integer(),
            nullable=False,
            comment="Index of the first effort waypoint in the activity GPS stream",
        ),
        sa.Column(
            "stream_index_end",
            sa.Integer(),
            nullable=False,
            comment="Index of the last effort waypoint in the activity GPS stream (inclusive)",
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
        sa.ForeignKeyConstraint(["segment_id"], ["segments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "segment_id",
            "activity_id",
            "direction",
            "pass_number",
            name="uq_segment_efforts_segment_activity_direction_pass",
        ),
        sa.CheckConstraint("direction IN ('forward', 'reverse')", name="ck_segment_efforts_direction"),
    )
    op.create_index(op.f("ix_segment_efforts_id"), "segment_efforts", ["id"], unique=False)
    op.create_index(op.f("ix_segment_efforts_segment_id"), "segment_efforts", ["segment_id"], unique=False)
    op.create_index(op.f("ix_segment_efforts_activity_id"), "segment_efforts", ["activity_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_segment_efforts_activity_id"), table_name="segment_efforts")
    op.drop_index(op.f("ix_segment_efforts_segment_id"), table_name="segment_efforts")
    op.drop_index(op.f("ix_segment_efforts_id"), table_name="segment_efforts")
    op.drop_table("segment_efforts")
    op.drop_index(op.f("ix_segments_created_by"), table_name="segments")
    op.drop_index(op.f("ix_segments_id"), table_name="segments")
    op.drop_table("segments")
