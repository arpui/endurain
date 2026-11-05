"""40: what the athlete says about a stretch the detector claimed

The detector's index is a hypothesis about ground; the only ground truth available
is the athlete's memory of the route. A mark ("yes, that is what it was", "it was
technical but not that much", "that was not technical at all") is stored against
the stretch so a calibration can be judged against labels instead of against a
hunch, and it is deliberately a small ordinal verdict rather than a 1-5 rating: an
invented scale would have to be translated back into the model's own terms.

A mark is kept per athlete and stretch (re-marking replaces), and it travels with
the section's position, so it can be reached again through the path the section was
matched to.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9f3b6c04a21"
down_revision: str | None = "c8e2a5b70d19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analysis_trail_marks",
        sa.Column("user_id", sa.Integer(), nullable=False, comment="Athlete judging the stretch"),
        sa.Column("activity_id", sa.Integer(), nullable=False, comment="Session the judged stretch belongs to"),
        sa.Column(
            "start_m",
            sa.Float(),
            nullable=False,
            comment="Ground position of the judged stretch, as the detector reported it",
        ),
        sa.Column("verdict", sa.String(length=16), nullable=False, comment="agree / overstated / wrong"),
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
        sa.PrimaryKeyConstraint("user_id", "activity_id", "start_m"),
        sa.CheckConstraint(
            "verdict IN ('agree', 'overstated', 'wrong')",
            name="ck_analysis_trail_marks_verdict",
        ),
    )
    op.create_index("ix_analysis_trail_marks_activity", "analysis_trail_marks", ["activity_id"])
    op.create_index("ix_analysis_trail_marks_user_verdict", "analysis_trail_marks", ["user_id", "verdict"])


def downgrade() -> None:
    op.drop_index("ix_analysis_trail_marks_user_verdict", table_name="analysis_trail_marks")
    op.drop_index("ix_analysis_trail_marks_activity", table_name="analysis_trail_marks")
    op.drop_table("analysis_trail_marks")
