"""37: keep the trail detector's observations, with the parameters behind them

The technical-terrain detector answers a question the athlete can only judge from
memory - was the *ground* costing here, or only the gradient? - and its weights
and threshold are still being calibrated against that memory. Reading them on
every request leaves nothing to compare a calibration against afterwards, so each
stretch is stored with the three components it weighed, the session's own
reference pace and a stamp of the parameters used. Rows written by an older model
stay next to the new ones instead of being rewritten, so a weight change can be
reviewed against what the same ground measured before it.

Only run sports produce rows; nothing is invented for a session without the
streams the detector needs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a6c1f8d43b07"
down_revision: str | None = "9b5e2c7a1d48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analysis_trail_sessions",
        sa.Column("user_id", sa.Integer(), nullable=False, comment="Athlete the observation belongs to"),
        sa.Column("activity_id", sa.Integer(), nullable=False, comment="Session measured"),
        sa.Column(
            "method", sa.String(length=64), nullable=False, comment="Detector parameters that produced the row, stamped"
        ),
        sa.Column(
            "v_base_ms",
            sa.Float(),
            nullable=False,
            server_default="0",
            comment="The session's own flat-ground reference pace",
        ),
        sa.Column(
            "cadence_available",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
            comment="Whether the stride terms could be used at all",
        ),
        sa.Column("total_m", sa.Float(), nullable=False, server_default="0", comment="Ground covered by the session"),
        sa.Column(
            "technical_m",
            sa.Float(),
            nullable=False,
            server_default="0",
            comment="Ground flagged technical over it",
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
        sa.PrimaryKeyConstraint("user_id", "activity_id", "method"),
    )
    op.create_table(
        "analysis_trail_sections",
        sa.Column("user_id", sa.Integer(), nullable=False, comment="Athlete the observation belongs to"),
        sa.Column("activity_id", sa.Integer(), nullable=False, comment="Session the stretch comes from"),
        sa.Column(
            "method", sa.String(length=64), nullable=False, comment="Detector parameters that produced the row, stamped"
        ),
        sa.Column(
            "start_m",
            sa.Float(),
            nullable=False,
            comment="Ground position where the stretch starts, metres from the start",
        ),
        sa.Column("length_m", sa.Float(), nullable=False, comment="Ground covered by the stretch"),
        sa.Column("grade_pct", sa.Float(), nullable=False, comment="Mean grade over it, percent"),
        sa.Column("avg_speed_ms", sa.Float(), nullable=False, comment="Mean ground speed through it"),
        sa.Column(
            "gap_deficit", sa.Float(), nullable=False, comment="Shortfall against the pace the grade explains (0-1)"
        ),
        sa.Column("cadence_jitter", sa.Float(), nullable=False, comment="Cadence instability over it (0-1)"),
        sa.Column("brake_score", sa.Float(), nullable=False, comment="Downhill braking share over it (0-1)"),
        sa.Column("score", sa.Float(), nullable=False, comment="Peak smoothed technicality index inside it"),
        sa.Column("direction", sa.String(length=8), nullable=False, comment="descent / ascent / flat by mean grade"),
        sa.Column(
            "v_base_ms",
            sa.Float(),
            nullable=False,
            comment="The session's own flat-ground reference pace",
        ),
        sa.Column(
            "cadence_available",
            sa.Boolean(),
            nullable=False,
            comment="Whether the stride terms could be used at all",
        ),
        sa.Column("total_m", sa.Float(), nullable=False, comment="Ground covered by the session"),
        sa.Column(
            "start_t",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Timestamp of the stretch's first sample, to draw its polyline",
        ),
        sa.Column(
            "end_t",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Timestamp of the stretch's last sample",
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
        sa.ForeignKeyConstraint(
            ["user_id", "activity_id", "method"],
            [
                "analysis_trail_sessions.user_id",
                "analysis_trail_sessions.activity_id",
                "analysis_trail_sessions.method",
            ],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "activity_id", "method", "start_m"),
        sa.CheckConstraint(
            "direction IN ('descent', 'ascent', 'flat')",
            name="ck_analysis_trail_sections_direction",
        ),
    )
    op.create_index(
        "ix_analysis_trail_sections_user_method",
        "analysis_trail_sections",
        ["user_id", "method"],
    )


def downgrade() -> None:
    op.drop_index("ix_analysis_trail_sections_user_method", table_name="analysis_trail_sections")
    op.drop_table("analysis_trail_sections")
    op.drop_table("analysis_trail_sessions")
