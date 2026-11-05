"""38: the cartographic side of the trail detector - cached OSM paths and their match

The detector answers what the ground cost the athlete; OpenStreetMap answers what
the ground is said to be (``sac_scale``, ``mtb:scale``, ``surface``,
``trail_visibility``). Keeping the two side by side is what makes the second
useful: where they disagree is either an unmapped gully or an over-cautious
tagging, and those disagreements are the evidence a calibration is made of.

The paths live in a table rather than being fetched at read time: the deployment
is a self-hosted LAN box with no reason to reach Overpass per request, and an
import is a maintenance action a human runs (``devscripts/osm-trail-cache.py``).
The extract's date is stored with the rows, so a later refresh can tell which
cartography a given observation was measured against.

Each stretch carries the way it matched and the offsets along it, which is what
turns the per-activity observations into a per-path knowledge base: asking "how
technical is this path, over the times I have crossed it" is a grouping, not a
new spatial join.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b7d0e4f92a15"
down_revision: str | None = "a6c1f8d43b07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analysis_osm_paths",
        sa.Column("way_id", sa.BigInteger(), nullable=False, comment="OpenStreetMap way id"),
        sa.Column("name", sa.String(length=256), nullable=True, comment="Way name, when mapped"),
        sa.Column("tags", postgresql.JSONB(), nullable=False, comment="Cartographic attributes the way carries"),
        sa.Column(
            "geometry",
            postgresql.JSONB(),
            nullable=False,
            comment="Ordered [[lat, lng], ...] polyline as the way is drawn",
        ),
        sa.Column("length_m", sa.Float(), nullable=False, comment="Length along the stored geometry"),
        sa.Column("min_lat", sa.Float(), nullable=False, comment="South edge of the geometry bounding box"),
        sa.Column("min_lng", sa.Float(), nullable=False, comment="West edge of the geometry bounding box"),
        sa.Column("max_lat", sa.Float(), nullable=False, comment="North edge of the geometry bounding box"),
        sa.Column("max_lng", sa.Float(), nullable=False, comment="East edge of the geometry bounding box"),
        sa.Column("cache_date", sa.Date(), nullable=False, comment="Day the extract was taken"),
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
        sa.PrimaryKeyConstraint("way_id"),
    )
    op.create_index("ix_analysis_osm_paths_bbox", "analysis_osm_paths", ["min_lat", "max_lat", "min_lng", "max_lng"])

    op.add_column(
        "analysis_trail_sections",
        sa.Column("osm_way_id", sa.BigInteger(), nullable=True, comment="Way the stretch was matched to"),
    )
    op.add_column(
        "analysis_trail_sections",
        sa.Column(
            "osm_start_m",
            sa.Float(),
            nullable=True,
            comment="Offset along the way where the stretch starts",
        ),
    )
    op.add_column(
        "analysis_trail_sections",
        sa.Column(
            "osm_end_m",
            sa.Float(),
            nullable=True,
            comment="Offset along the way where the stretch ends",
        ),
    )
    op.add_column(
        "analysis_trail_sections",
        sa.Column(
            "osm_covered_m",
            sa.Float(),
            nullable=True,
            comment="Ground of the stretch the way accounts for",
        ),
    )
    op.add_column(
        "analysis_trail_sections",
        sa.Column(
            "osm_cache_date",
            sa.Date(),
            nullable=True,
            comment="Extract the stretch was matched against (also records an honest miss)",
        ),
    )
    op.create_foreign_key(
        "fk_analysis_trail_sections_osm_way",
        "analysis_trail_sections",
        "analysis_osm_paths",
        ["osm_way_id"],
        ["way_id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_analysis_trail_sections_osm_way", "analysis_trail_sections", ["osm_way_id"])


def downgrade() -> None:
    op.drop_index("ix_analysis_trail_sections_osm_way", table_name="analysis_trail_sections")
    op.drop_constraint("fk_analysis_trail_sections_osm_way", "analysis_trail_sections", type_="foreignkey")
    op.drop_column("analysis_trail_sections", "osm_cache_date")
    op.drop_column("analysis_trail_sections", "osm_covered_m")
    op.drop_column("analysis_trail_sections", "osm_end_m")
    op.drop_column("analysis_trail_sections", "osm_start_m")
    op.drop_column("analysis_trail_sections", "osm_way_id")
    op.drop_index("ix_analysis_osm_paths_bbox", table_name="analysis_osm_paths")
    op.drop_table("analysis_osm_paths")
