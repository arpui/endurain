"""46: mapped water points for the route guide

A route guide says where water is. The points come from the same imported
cartography as the ways - drinking water as ``font``, unvouched sources as
``spring`` - and are stored the same way: by a maintenance import, never
fetched per request.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c3f9a1d4e62"
down_revision: str | None = "52a614e48605"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analysis_osm_waters",
        sa.Column("osm_id", sa.BigInteger(), nullable=False, comment="OpenStreetMap node id"),
        sa.Column("kind", sa.String(length=8), nullable=False, comment="font / spring"),
        sa.Column("name", sa.String(length=256), nullable=True, comment="Name, when mapped"),
        sa.Column("lat", sa.Float(), nullable=False, comment="Latitude"),
        sa.Column("lng", sa.Float(), nullable=False, comment="Longitude"),
        sa.Column("cache_date", sa.Date(), nullable=False, comment="Day the extract was taken"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("kind IN ('font', 'spring')", name="ck_analysis_osm_waters_kind"),
        sa.PrimaryKeyConstraint("osm_id"),
    )
    op.create_index("ix_analysis_osm_waters_position", "analysis_osm_waters", ["lat", "lng"])


def downgrade() -> None:
    op.drop_index("ix_analysis_osm_waters_position", table_name="analysis_osm_waters")
    op.drop_table("analysis_osm_waters")
