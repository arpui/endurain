"""44: segment crossings and the cells segments cover

The network grows a named layer: each stored segment effort is projected onto
both its track and its segment, so claims can aggregate over the athlete's
technical observation without re-matching geometry; and each segment stamps
the grid cells it covers, which the fitted route drawer prefers while walking
the personal graph.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5da71489f5c"
down_revision: str | None = "e79b18f3d53e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analysis_segment_crossings",
        sa.Column("segment_id", sa.Integer(), nullable=False, comment="Segment traversed"),
        sa.Column("activity_id", sa.Integer(), nullable=False, comment="Session of the crossing"),
        sa.Column("pass_number", sa.Integer(), nullable=False, comment="Pass of the activity over it"),
        sa.Column("user_id", sa.Integer(), nullable=False, comment="Athlete"),
        sa.Column("along_start_m", sa.Float(), nullable=False, comment="Along-segment start offset"),
        sa.Column("along_end_m", sa.Float(), nullable=False, comment="Along-segment end offset"),
        sa.Column("track_start_m", sa.Float(), nullable=False, comment="Along-track start offset"),
        sa.Column("track_end_m", sa.Float(), nullable=False, comment="Along-track end offset"),
        sa.Column("direction", sa.String(length=7), nullable=False, primary_key=True, comment="forward / reverse"),
        sa.Column("grade_pct", sa.Float(), nullable=True, comment="Mean signed grade of the pass"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["segment_id"], ["segments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("segment_id", "activity_id", "pass_number", "direction"),
    )
    op.create_index(
        "ix_analysis_segment_crossings_user_segment", "analysis_segment_crossings", ["user_id", "segment_id"]
    )
    op.create_index("ix_analysis_segment_crossings_activity", "analysis_segment_crossings", ["activity_id"])

    op.create_table(
        "analysis_segment_cells",
        sa.Column("segment_id", sa.Integer(), nullable=False, comment="Segment covering it"),
        sa.Column("x", sa.Integer(), nullable=False, comment="Grid column"),
        sa.Column("y", sa.Integer(), nullable=False, comment="Grid row"),
        sa.ForeignKeyConstraint(["segment_id"], ["segments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("segment_id", "x", "y"),
    )


def downgrade() -> None:
    op.drop_table("analysis_segment_cells")
    op.drop_index("ix_analysis_segment_crossings_activity", table_name="analysis_segment_crossings")
    op.drop_index("ix_analysis_segment_crossings_user_segment", table_name="analysis_segment_crossings")
    op.drop_table("analysis_segment_crossings")
