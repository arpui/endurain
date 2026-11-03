"""33: tag power streams with their provenance

Revision ID: 6c9f2a48d1b7
Revises: f0a1d3c9e572
Create Date: 2026-11-03 00:00:00.000000

Pod-metered (Stryd) running power sits on a different absolute scale than the
athlete's later native run power, and the only robust discriminator is the FIT
field the reading arrived in (standard ``power`` vs the ``Power`` developer
field). Provenance belongs next to the data it describes, so it is stored on
the power stream row and the read path recalibrates tagged streams onto the
current scale while the stored waypoints stay raw. Nullable: non-power streams
and legacy rows carry nothing until labeled.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6c9f2a48d1b7"
down_revision: str | None = "f0a1d3c9e572"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "activities_streams",
        sa.Column(
            "power_source",
            sa.String(),
            nullable=True,
            comment="Power stream provenance: stryd (pod developer field) or device (standard field)",
        ),
    )


def downgrade() -> None:
    op.drop_column("activities_streams", "power_source")
