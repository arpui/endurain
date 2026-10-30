"""28: create analysis settings, activity loads and duration peaks

Revision ID: b7f3c2194da6
Revises: d54bdd779392
Create Date: 2026-10-30 00:00:00.000000

Training-load analysis stores three things. ``analysis_settings`` is one row per
athlete holding the parameters every computation reads: the fatigue/fitness time
constants, the primary load formula, and the thresholds (derived or typed) that a
load is a fraction of. ``analysis_activity_loads`` is one row per (activity, formula)
so the formulas coexist and a stored load keeps the threshold it was divided by --
changing a threshold is a recompute, never a reinterpretation. The owner, local
calendar day and sport group are copied onto the row at measure time so the daily
fitness series groups them in one indexed read; an edit to what they derive from
re-measures the activity. ``analysis_duration_peaks`` caches each athlete's best
sustained average per sport and duration, which is both the duration-intensity curve
and where a derived critical power comes from.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7f3c2194da6"
down_revision: str | None = "d54bdd779392"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_METHODS = "('coggan_tss', 'skiba_score', 'hr_load', 'trimp')"


def upgrade() -> None:
    op.create_table(
        "analysis_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False, comment="Athlete these parameters belong to"),
        sa.Column(
            "acute_days",
            sa.Integer(),
            server_default="7",
            nullable=False,
            comment="Fatigue time constant in days",
        ),
        sa.Column(
            "chronic_days",
            sa.Integer(),
            server_default="42",
            nullable=False,
            comment="Fitness time constant in days",
        ),
        sa.Column(
            "primary_method",
            sa.String(length=32),
            server_default="coggan_tss",
            nullable=False,
            comment="Load formula used when one load is asked for",
        ),
        sa.Column(
            "resting_hr",
            sa.Integer(),
            nullable=True,
            comment="Resting heart rate in bpm stated by the athlete, when known",
        ),
        sa.Column(
            "thresholds",
            sa.JSON(),
            nullable=True,
            comment="Power and heart-rate thresholds, with their provenance",
        ),
        sa.Column(
            "method_params",
            sa.JSON(),
            nullable=True,
            comment="Per-formula parameters keyed by method name",
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_analysis_settings_user"),
        sa.CheckConstraint(f"primary_method IN {_METHODS}", name="ck_analysis_settings_primary_method"),
    )
    op.create_index(op.f("ix_analysis_settings_id"), "analysis_settings", ["id"], unique=False)

    op.create_table(
        "analysis_activity_loads",
        sa.Column("activity_id", sa.Integer(), nullable=False, comment="Activity this load belongs to"),
        sa.Column(
            "user_id",
            sa.Integer(),
            nullable=False,
            comment="Activity owner, copied so daily series aggregate in one read",
        ),
        sa.Column("method", sa.String(length=32), nullable=False, comment="Formula that produced this load"),
        sa.Column(
            "load",
            sa.Float(),
            nullable=False,
            comment="Stress score: 100 is one hour held at the athlete's threshold",
        ),
        sa.Column("intensity_factor", sa.Float(), nullable=True, comment="Intensity the load was derived from"),
        sa.Column("threshold_used", sa.Float(), nullable=True, comment="Threshold value the formula divided by"),
        sa.Column(
            "duration_s",
            sa.Float(),
            nullable=False,
            comment="Active time the load was computed over, in seconds",
        ),
        sa.Column(
            "load_date",
            sa.Date(),
            nullable=True,
            comment="Calendar day the activity started, in its own timezone",
        ),
        sa.Column(
            "sport",
            sa.String(length=32),
            nullable=True,
            comment="Sport group of the activity type, when categorized",
        ),
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("activity_id", "method"),
        sa.CheckConstraint(f"method IN {_METHODS}", name="ck_analysis_activity_loads_method"),
    )
    op.create_index(
        op.f("ix_analysis_activity_loads_activity_id"),
        "analysis_activity_loads",
        ["activity_id"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_activity_loads_user_method_date",
        "analysis_activity_loads",
        ["user_id", "method", "load_date"],
        unique=False,
    )

    op.create_table(
        "analysis_duration_peaks",
        sa.Column("user_id", sa.Integer(), nullable=False, comment="Athlete this curve point belongs to"),
        sa.Column("sport", sa.String(length=32), nullable=False, comment="Sport group the curve is drawn for"),
        sa.Column(
            "duration_s",
            sa.Integer(),
            nullable=False,
            comment="Duration the average was held for, in seconds",
        ),
        sa.Column("value", sa.Float(), nullable=False, comment="Best sustained average at that duration"),
        sa.Column(
            "activity_id",
            sa.Integer(),
            nullable=True,
            comment="Activity holding this best average",
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
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("user_id", "sport", "duration_s"),
    )


def downgrade() -> None:
    op.drop_table("analysis_duration_peaks")
    op.drop_index(op.f("ix_analysis_activity_loads_activity_id"), table_name="analysis_activity_loads")
    op.drop_table("analysis_activity_loads")
    op.drop_index(op.f("ix_analysis_settings_id"), table_name="analysis_settings")
    op.drop_table("analysis_settings")
