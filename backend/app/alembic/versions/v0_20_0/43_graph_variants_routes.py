"""43: the personal ground graph, variant verdicts, and saved routes

The graph moves the athlete's crossed ground (cells and directed transitions)
from offline experiments into the database, so the variant search and the route
designer can run on the application. Variant marks record what the athlete says
about each suggested alternative: real ground or a GPS artifact, permanently.
Routes store designed itineraries - a polyline plus the ways it walks - waiting
to be exported as navigable courses.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e79b18f3d53e"
down_revision: str | None = "428fa94ac0ad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analysis_graph_nodes",
        sa.Column("user_id", sa.Integer(), nullable=False, comment="Athlete whose tracks crossed the cell"),
        sa.Column("x", sa.Integer(), nullable=False, comment="Grid column of the cell"),
        sa.Column("y", sa.Integer(), nullable=False, comment="Grid row of the cell"),
        sa.Column("lat", sa.Float(), nullable=False, comment="Centroid latitude"),
        sa.Column("lng", sa.Float(), nullable=False, comment="Centroid longitude"),
        sa.Column("ele", sa.Float(), nullable=False, server_default="0", comment="Mean elevation of the cell"),
        sa.Column("visits", sa.Integer(), nullable=False, server_default="0", comment="Track samples in the cell"),
        sa.Column("grade", sa.String(length=8), nullable=False, server_default="", comment="hard / tech / empty"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "x", "y"),
        sa.CheckConstraint("grade IN ('', 'tech', 'hard')", name="ck_analysis_graph_nodes_grade"),
    )
    op.create_index("ix_analysis_graph_nodes_user_cell", "analysis_graph_nodes", ["user_id", "x", "y"])

    op.create_table(
        "analysis_graph_edges",
        sa.Column("user_id", sa.Integer(), nullable=False, comment="Athlete whose tracks crossed the edge"),
        sa.Column("ax", sa.Integer(), nullable=False, comment="Origin grid column"),
        sa.Column("ay", sa.Integer(), nullable=False, comment="Origin grid row"),
        sa.Column("bx", sa.Integer(), nullable=False, comment="Destination grid column"),
        sa.Column("by", sa.Integer(), nullable=False, comment="Destination grid row"),
        sa.Column("length", sa.Float(), nullable=False, server_default="0", comment="Accumulated ground length"),
        sa.Column("gain", sa.Float(), nullable=False, server_default="0", comment="Accumulated elevation gain"),
        sa.Column("loss", sa.Float(), nullable=False, server_default="0", comment="Accumulated elevation loss"),
        sa.Column("passes", sa.Integer(), nullable=False, server_default="0", comment="Times traversed"),
        sa.Column("step_max", sa.Float(), nullable=False, server_default="0", comment="Worst raw step producing it"),
        sa.Column(
            "on_path", sa.Integer(), nullable=False, server_default="0", comment="Cartographically parallel traversals"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "ax", "ay", "bx", "by"),
    )
    op.create_index("ix_analysis_graph_edges_user_origin", "analysis_graph_edges", ["user_id", "ax", "ay"])

    op.create_table(
        "analysis_variant_marks",
        sa.Column("user_id", sa.Integer(), nullable=False, comment="Athlete judging"),
        sa.Column("activity_id", sa.Integer(), nullable=False, comment="Planned route judged"),
        sa.Column("split_m", sa.Float(), nullable=False, comment="Junction position along the route judged at"),
        sa.Column("verdict", sa.String(length=8), nullable=False, comment="real / falsa"),
        sa.Column("note", sa.String(length=256), nullable=True, comment="Optional remark"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "activity_id", "split_m"),
        sa.CheckConstraint("verdict IN ('real', 'falsa')", name="ck_analysis_variant_marks_verdict"),
    )
    op.create_index("ix_analysis_variant_marks_activity", "analysis_variant_marks", ["activity_id"])

    op.create_table(
        "analysis_routes",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="Route id"),
        sa.Column("user_id", sa.Integer(), nullable=False, comment="Athlete who designed it"),
        sa.Column("name", sa.String(length=128), nullable=False, comment="Route name"),
        sa.Column("geometry", postgresql.JSONB(), nullable=False, comment="Ordered [lat, lng] polyline"),
        sa.Column("distance_m", sa.Float(), nullable=False, server_default="0", comment="Ground covered"),
        sa.Column("gain_m", sa.Float(), nullable=False, server_default="0", comment="Elevation gain"),
        sa.Column("notes", sa.Text(), nullable=True, comment="Free-text remarks"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analysis_routes_user", "analysis_routes", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_analysis_routes_user", table_name="analysis_routes")
    op.drop_table("analysis_routes")
    op.drop_index("ix_analysis_variant_marks_activity", table_name="analysis_variant_marks")
    op.drop_table("analysis_variant_marks")
    op.drop_index("ix_analysis_graph_edges_user_origin", table_name="analysis_graph_edges")
    op.drop_table("analysis_graph_edges")
    op.drop_index("ix_analysis_graph_nodes_user_cell", table_name="analysis_graph_nodes")
    op.drop_table("analysis_graph_nodes")
