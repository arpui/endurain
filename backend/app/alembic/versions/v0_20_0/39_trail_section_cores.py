"""39: the hard block inside a measured stretch

An athlete judging a stretch over 200-300 m cannot call the whole of it extreme:
a long descent usually holds one block of stones and a lot of runnable ground
around it. So the detector now records, inside each stretch, the contiguous run of
windows that clear the hard ratio - where the very technical ground actually is -
rather than letting one peak number describe a kilometre and a half.

Nothing is renamed or recomputed here: stretches measured before keep their
values, and the three columns simply stay null until the observation is taken
again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8e2a5b70d19"
down_revision: str | None = "b7d0e4f92a15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "analysis_trail_sections",
        sa.Column(
            "core_start_m",
            sa.Float(),
            nullable=True,
            comment="Ground position where the hard block inside the stretch starts",
        ),
    )
    op.add_column(
        "analysis_trail_sections",
        sa.Column("core_end_m", sa.Float(), nullable=True, comment="Ground position where that block ends"),
    )
    op.add_column(
        "analysis_trail_sections",
        sa.Column("core_score", sa.Float(), nullable=True, comment="Peak index inside the hard block"),
    )


def downgrade() -> None:
    op.drop_column("analysis_trail_sections", "core_score")
    op.drop_column("analysis_trail_sections", "core_end_m")
    op.drop_column("analysis_trail_sections", "core_start_m")
