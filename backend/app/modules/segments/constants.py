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
