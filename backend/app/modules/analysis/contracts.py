"""Plain-data contracts the analysis layers and its published surface pass around.

Values moving between service and crud are plain dataclasses, never ORM rows: the
persistence layer keeps its models to itself and the service layer never learns which
table a number was written to. The same rule covers what leaves the module: another
module receives one of these dataclasses through ``integration_service``.
"""

from dataclasses import dataclass
from datetime import date, datetime

import modules.analysis.constants as analysis_constants


@dataclass(frozen=True)
class LoadRecord:
    """One computed load for one activity.

    Attributes:
        method: Which formula produced it (part of the stored row's identity).
        load: Stress in the shared scale (100 = one hour at threshold).
        intensity_factor: Intensity the load was derived from, when the formula has one.
        threshold_used: Threshold the formula divided by, recorded so a later change
            of threshold cannot silently reinterpret the row.
    """

    method: analysis_constants.LoadMethod
    load: float
    intensity_factor: float | None = None
    threshold_used: float | None = None


@dataclass(frozen=True)
class PeakRecord:
    """One point of an athlete's duration-intensity curve.

    Attributes:
        duration_s: Duration the best average was held for.
        value: Best sustained average at that duration, in the sport's power unit.
        activity_id: Activity holding that best average, so the curve can be traced
            back to a session; ``None`` when the holder was not recorded.
    """

    duration_s: int
    value: float
    activity_id: int | None = None


@dataclass(frozen=True)
class PowerThreshold:
    """A power threshold used by the power formulas, with its provenance.

    Attributes:
        value_w: Threshold power in watts (critical power, or the athlete's FTP).
        w_prime_j: Anaerobic work capacity fitted alongside it, in joules. An index
            for comparing the athlete with themselves: its absolute value depends on
            the duration range the fit used.
        source: Whether it was derived from records or entered by the athlete.
        cv_pct: Fit error as a percentage of the mean (only for derived values).
    """

    value_w: float
    w_prime_j: float | None = None
    source: analysis_constants.ThresholdSource = analysis_constants.ThresholdSource.AUTO
    cv_pct: float | None = None


@dataclass(frozen=True)
class HeartRateThreshold:
    """A threshold heart rate, with its provenance.

    Attributes:
        value_bpm: Threshold heart rate in beats per minute.
        source: Whether it was derived from the best sustained average or entered by
            the athlete.
    """

    value_bpm: float
    source: analysis_constants.ThresholdSource = analysis_constants.ThresholdSource.AUTO


@dataclass(frozen=True)
class WindowStats:
    """Aggregate reads over one time window of an activity's own streams.

    Attributes:
        avg_power_w: Mean power over the window's samples, ``None`` without a
            power stream.
        avg_hr_bpm: Mean heart rate over the window, sensor dropouts excluded,
            ``None`` without a heart-rate stream.
        rss: The shared-scale aRSS score of the window, from the intensity chain
            (watts, grade-adjusted pace, heart rate); ``None`` when nothing scored.
        rss_source: Which signal scored it (``power``, ``pace``, ``hr``).
        rss_per_min: That score per minute of the window; ``None`` when there is
            no score.
        source_scores: The window scored by *every* available signal
            (``power``/``pace``/``hr``), for the side-by-side benchmark.
        rap_ms: Grade-equivalent pace (GAP/RAP) over the window, ``None`` when the
            athlete has no critical speed for the sport.
    """

    avg_power_w: float | None = None
    avg_hr_bpm: float | None = None
    rss: float | None = None
    rss_source: str | None = None
    rss_per_min: float | None = None
    source_scores: dict[str, float] | None = None
    rap_ms: float | None = None


@dataclass(frozen=True)
class PeakFlagRecord:
    """One session the peak builder held out of the curve, and why.

    Attributes:
        activity_id: The refused session.
        started_on: Day of the session, kept for the notice.
        duration_s: Duration of the strongest violation, in seconds.
        value_w: The refused best average, in watts.
        session_np_w: The session's own normalized power, in watts.
        ratio: ``value_w / session_np_w``.
        cap: The tier ratio the session exceeded.
    """

    activity_id: int
    started_on: date
    duration_s: int
    value_w: float
    session_np_w: float
    ratio: float
    cap: float


@dataclass(frozen=True)
class ThresholdFitRecord:
    """One persisted per-session threshold fit serving as temporal evidence.

    Attributes:
        activity_id: The session the fit came from.
        started_on: Day the fit supports.
        value: Fitted threshold, in the measure's units.
    """

    activity_id: int
    started_on: date
    value: float
    w_prime: float = 0.0
    cv_pct: float = 0.0
    sustained: float = 0.0
    power_source: str | None = None


@dataclass(frozen=True)
class TrailSessionRecord:
    """One run activity measured by the technical-terrain detector, once per model.

    Attributes:
        v_base_ms: The session's own flat-ground reference pace.
        cadence_available: Whether the stride terms could be used at all.
        total_m: Ground covered by the session.
        technical_m: Ground flagged technical over it.
    """

    v_base_ms: float
    cadence_available: bool
    total_m: float
    technical_m: float


@dataclass(frozen=True)
class TrailSectionRecord:
    """One technical stretch the trail detector read in one session.

    The three components are stored next to the index they produced so a later
    calibration can be compared against what the same ground measured before it.

    Attributes:
        start_m: Ground position where the stretch starts, metres from the start.
        length_m: Ground covered by the stretch.
        grade_pct: Mean grade over it, percent.
        avg_speed_ms: Mean ground speed through it.
        gap_deficit: Shortfall against the pace the grade explains (0-1).
        cadence_jitter: Cadence instability over it (0-1).
        brake_score: Downhill braking share over it (0-1).
        score: Peak smoothed technicality index inside it.
        direction: ``descent`` / ``ascent`` / ``flat`` by mean grade.
        core_start_m: Ground position where the hard block inside starts, if any.
        core_end_m: Ground position where that block ends.
        core_score: Peak index inside the hard block.
        v_base_ms: The session's own flat-ground reference pace.
        cadence_available: Whether the stride terms could be used at all.
        total_m: Ground covered by the session.
        start_t: Timestamp of the stretch's first sample.
        end_t: Timestamp of its last sample.
    """

    start_m: float
    length_m: float
    grade_pct: float
    avg_speed_ms: float
    gap_deficit: float
    cadence_jitter: float
    brake_score: float
    score: float
    direction: str
    core_start_m: float | None
    core_end_m: float | None
    core_score: float | None
    v_base_ms: float
    cadence_available: bool
    total_m: float
    start_t: datetime
    end_t: datetime


@dataclass(frozen=True)
class OsmPathRecord:
    """One OpenStreetMap way, as an extract described it.

    Attributes:
        way_id: The OpenStreetMap way id.
        name: Way name, when mapped.
        tags: The cartographic attributes the way carries.
        geometry: Ordered ``(lat, lng)`` pairs as the way is drawn.
        length_m: Length along the stored geometry.
        min_lat: South edge of the geometry bounding box.
        min_lng: West edge of the geometry bounding box.
        max_lat: North edge of the geometry bounding box.
        max_lng: East edge of the geometry bounding box.
        cache_date: Day the extract was taken.
    """

    way_id: int
    name: str | None
    tags: dict[str, str]
    geometry: list[tuple[float, float]]
    length_m: float
    min_lat: float
    min_lng: float
    max_lat: float
    max_lng: float
    cache_date: date


@dataclass(frozen=True)
class OsmWaterRecord:
    """One mapped water point, as an extract described it.

    Attributes:
        osm_id: The OpenStreetMap node id.
        kind: ``font`` (drinking water) or ``spring`` (a source the map does not
            promise is drinkable).
        name: Its name, when mapped.
        lat: Latitude.
        lng: Longitude.
        cache_date: Day the extract was taken.
    """

    osm_id: int
    kind: str
    name: str | None
    lat: float
    lng: float
    cache_date: date


@dataclass(frozen=True)
class OsmMatchRecord:
    """Which mapped way a measured stretch lies on, and where along it.

    Attributes:
        way_id: The matched way.
        start_m: Offset along the way of the stretch's first point.
        end_m: Offset along the way of its last point.
        covered_m: Ground of the stretch the way accounts for.
    """

    way_id: int
    start_m: float
    end_m: float
    covered_m: float


@dataclass(frozen=True)
class WayCrossingRecord:
    """One pass of an activity's track over one mapped way.

    Attributes:
        way_id: The matched way.
        start_m: Offset along the way where the crossing starts, in drawn order.
        end_m: Offset along the way where it ends.
        covered_m: Ground of the crossing the way accounts for.
    """

    way_id: int
    start_m: float
    end_m: float
    covered_m: float
