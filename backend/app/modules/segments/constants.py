"""Domain constants for the segments module.

The direction and source vocabularies are stated once, here, rather than as raw
literals in each layer: ``models`` stamps the defaults and the check constraints,
``crud`` filters on them, ``service`` compares against them and ``schema`` types
the fields clients see.
"""

from enum import StrEnum


class SegmentDirection(StrEnum):
    """Direction of an effort relative to the stored segment geometry.

    The segment geometry has one canonical order (start → end). An effort that
    travels it in that order is ``forward`` — the only direction that counts as
    a real effort, matching how Strava treats a climb ridden backwards. An
    effort in the opposite order is ``reverse``, stored for information only.

    Attributes:
        FORWARD: The effort follows the stored geometry order.
        REVERSE: The effort runs against the stored geometry order.
    """

    FORWARD = "forward"
    REVERSE = "reverse"


class SegmentSource(StrEnum):
    """Where a segment definition came from.

    Attributes:
        STRAVA: Imported from a Strava segment (``source_id`` is the Strava id).
        MANUAL: Drawn or defined inside Endurain (no external id).
    """

    STRAVA = "strava"
    MANUAL = "manual"


class MatchOutcome(StrEnum):
    """Verdict of one matcher attempt: one activity against one segment.

    Produced by the matcher's audit path for every segment that survives the
    bounding-box pre-filter, so a non-match always carries its reason.

    Attributes:
        MATCHED: At least one plausible traverse was found.
        NO_GEOMETRY: The stored polyline has fewer than two points.
        NO_WAYPOINTS: The activity GPS stream is too short to traverse anything.
        BBOX_FAR: The activity bounding box cannot reach the segment's.
        NO_CANDIDATES: No waypoint came within the match radius of the geometry.
        NO_PASSES: Coincident waypoints exist but never formed a pass long enough.
        SPAN_SHORT: Passes exist, but none extended over the required span.
        NO_PLAUSIBLE: Full-span passes existed but failed the time/speed checks.
    """

    MATCHED = "matched"
    NO_GEOMETRY = "no_geometry"
    NO_WAYPOINTS = "no_waypoints"
    BBOX_FAR = "bbox_far"
    NO_CANDIDATES = "no_candidates"
    NO_PASSES = "no_passes"
    SPAN_SHORT = "span_short"
    NO_PLAUSIBLE = "no_plausible"
