"""45: variant verdicts on designed routes; routes remember their source

Verdicts stop being tied to recorded activities alone: a designed route runs
its own variant search and the athlete judges those variants on it. The mark
is re-keyed to an activity OR a route (never both), and saved routes keep a
memory of the activity they were saved from, so course export can carry the
detected stretches of a real track without guessing them on a drawn one.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "52a614e48605"
down_revision: str | None = "e5da71489f5c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "analysis_routes",
        sa.Column(
            "source_activity_id", sa.Integer(), nullable=True, comment="Recorded track this route was saved from"
        ),
    )
    op.create_foreign_key(
        "fk_analysis_routes_source_activity",
        "analysis_routes",
        "activities",
        ["source_activity_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.alter_column("analysis_variant_marks", "activity_id", existing_type=sa.Integer(), nullable=True)
    op.add_column(
        "analysis_variant_marks", sa.Column("route_id", sa.Integer(), nullable=True, comment="Designed route judged")
    )
    op.create_foreign_key(
        "fk_analysis_variant_marks_route",
        "analysis_variant_marks",
        "analysis_routes",
        ["route_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # surrogate key: the natural key now lives in two mutually exclusive halves
    op.execute("CREATE SEQUENCE analysis_variant_marks_id_seq")
    op.execute(
        "ALTER TABLE analysis_variant_marks ADD COLUMN id bigint NOT NULL DEFAULT nextval('analysis_variant_marks_id_seq')"
    )
    op.execute("ALTER SEQUENCE analysis_variant_marks_id_seq OWNED BY analysis_variant_marks.id")
    op.execute("ALTER TABLE analysis_variant_marks DROP CONSTRAINT analysis_variant_marks_pkey")
    op.execute("ALTER TABLE analysis_variant_marks ADD CONSTRAINT analysis_variant_marks_pkey PRIMARY KEY (id)")
    op.create_check_constraint(
        "ck_analysis_variant_marks_one_plan",
        "analysis_variant_marks",
        "(activity_id IS NULL) <> (route_id IS NULL)",
    )
    op.create_index(
        "uq_analysis_variant_marks_activity_split",
        "analysis_variant_marks",
        ["user_id", "activity_id", "split_m"],
        unique=True,
        postgresql_where=sa.text("activity_id IS NOT NULL"),
    )
    op.create_index(
        "uq_analysis_variant_marks_route_split",
        "analysis_variant_marks",
        ["user_id", "route_id", "split_m"],
        unique=True,
        postgresql_where=sa.text("route_id IS NOT NULL"),
    )
    op.create_index("ix_analysis_variant_marks_route", "analysis_variant_marks", ["route_id"])


def downgrade() -> None:
    op.drop_index("ix_analysis_variant_marks_route", table_name="analysis_variant_marks")
    op.drop_index("uq_analysis_variant_marks_route_split", table_name="analysis_variant_marks")
    op.drop_index("uq_analysis_variant_marks_activity_split", table_name="analysis_variant_marks")
    op.drop_constraint("ck_analysis_variant_marks_one_plan", "analysis_variant_marks", type_="check")
    op.execute("DELETE FROM analysis_variant_marks WHERE route_id IS NOT NULL")
    op.execute("ALTER TABLE analysis_variant_marks DROP CONSTRAINT analysis_variant_marks_pkey")
    op.execute("ALTER TABLE analysis_variant_marks DROP COLUMN id")
    op.execute("DROP SEQUENCE analysis_variant_marks_id_seq")
    op.execute("ALTER TABLE analysis_variant_marks ALTER COLUMN activity_id SET NOT NULL")
    op.execute(
        "ALTER TABLE analysis_variant_marks ADD CONSTRAINT analysis_variant_marks_pkey PRIMARY KEY (user_id, activity_id, split_m)"
    )
    op.drop_constraint("fk_analysis_variant_marks_route", "analysis_variant_marks", type_="foreignkey")
    op.drop_column("analysis_variant_marks", "route_id")
    op.drop_constraint("fk_analysis_routes_source_activity", "analysis_routes", type_="foreignkey")
    op.drop_column("analysis_routes", "source_activity_id")
