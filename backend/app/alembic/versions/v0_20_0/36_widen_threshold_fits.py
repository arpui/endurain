"""36: store every session fit, not only the gated evidence

Revision ID: 9b5e2c7a1d48
Revises: 8f4a3d07b2c6
Create Date: 2026-11-05 00:00:00.000000

The trend chart plots every session its curve can be fitted for, while the
temporal envelope keeps only the quality-gated ones - the same rows answer
both when the quality numbers travel with the fit. Widen the table: the
fit's error, its anaerobic index, the session's best sustained window (the
ceiling the gate checks against) and the power provenance. Quality is then
decided at read time; no request rereads streams for the chart.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9b5e2c7a1d48"
down_revision: str | None = "8f4a3d07b2c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "analysis_threshold_fits",
        sa.Column(
            "w_prime",
            sa.Float(),
            server_default="0",
            nullable=False,
            comment="Anaerobic capacity from the fit (joules or metres): an index tied to the fit window",
        ),
    )
    op.add_column(
        "analysis_threshold_fits",
        sa.Column(
            "cv_pct",
            sa.Float(),
            server_default="0",
            nullable=False,
            comment="Regression error of the fit, percent of the mean",
        ),
    )
    op.add_column(
        "analysis_threshold_fits",
        sa.Column(
            "sustained",
            sa.Float(),
            server_default="0",
            nullable=False,
            comment="Best sustained window average inside the fit range, the ceiling the fit must not overshoot",
        ),
    )
    op.add_column(
        "analysis_threshold_fits",
        sa.Column(
            "power_source",
            sa.String(length=32),
            nullable=True,
            comment="Where the session's watts came from, when tagged",
        ),
    )


def downgrade() -> None:
    op.drop_column("analysis_threshold_fits", "power_source")
    op.drop_column("analysis_threshold_fits", "sustained")
    op.drop_column("analysis_threshold_fits", "cv_pct")
    op.drop_column("analysis_threshold_fits", "w_prime")
