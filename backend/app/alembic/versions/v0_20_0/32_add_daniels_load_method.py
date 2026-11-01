"""32: widen the analysis method vocabulary to Daniels points

Revision ID: f0a1d3c9e572
Revises: e3f6a04b28c1
Create Date: 2026-11-01 00:00:00.000000

The stored load methods and the settings' primary method are checked against a
list of formula keys. The list grows by one: Daniels points, intensity to the
fourth power, an athlete without a power meter's pace-side companion to the
exponential family. No row changes and no column changes, so the two check
constraints are simply swapped for wider ones; the old keys keep every row
they had.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f0a1d3c9e572"
down_revision: str | None = "e3f6a04b28c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RSS_METHODS = (
    "('coggan_tss', 'skiba_score', 'hr_load', 'trimp', 'rss_total', 'rss_up', 'rss_high', 'rss_mid', 'rss_low')"
)
_WIDER_METHODS = (
    "('coggan_tss', 'skiba_score', 'hr_load', 'trimp', 'rss_total', 'rss_up', 'rss_high', 'rss_mid', 'rss_low', "
    "'daniels_points')"
)


def _swap_constraint(table: str, name: str, column: str, methods: str) -> None:
    """Replace one method check constraint with one allowing ``methods``."""
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN {methods}")


def upgrade() -> None:
    _swap_constraint("analysis_settings", "ck_analysis_settings_primary_method", "primary_method", _WIDER_METHODS)
    _swap_constraint("analysis_activity_loads", "ck_analysis_activity_loads_method", "method", _WIDER_METHODS)


def downgrade() -> None:
    _swap_constraint("analysis_settings", "ck_analysis_settings_primary_method", "primary_method", _RSS_METHODS)
    _swap_constraint("analysis_activity_loads", "ck_analysis_activity_loads_method", "method", _RSS_METHODS)
