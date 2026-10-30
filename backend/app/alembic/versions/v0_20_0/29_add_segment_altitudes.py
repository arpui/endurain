"""29: store per-point segment altitudes

Revision ID: 4a7e1b93c8d2
Revises: 9b1c4d7e2a5f
Create Date: 2026-10-30 00:00:00.000000

``elevation_gain_m`` answers "how much climbing", but a segment page also shows
the profile, which needs one altitude per geometry point. It arrives with the
imported polyline (Strava streams carry it, a GPX has ``<ele>``), so it is kept
index-aligned with ``geometry`` rather than re-derived. Nullable: a segment
drawn by hand has no elevation data.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4a7e1b93c8d2"
down_revision: str | None = "9b1c4d7e2a5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "segments",
        sa.Column(
            "altitudes",
            sa.JSON(),
            nullable=True,
            comment="Per-point altitude in meters, index-aligned with geometry, when the source provides it",
        ),
    )


def downgrade() -> None:
    op.drop_column("segments", "altitudes")
