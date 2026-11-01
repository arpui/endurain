"""Plain-data contracts the analysis layers and its published surface pass around.

Values moving between service and crud are plain dataclasses, never ORM rows: the
persistence layer keeps its models to itself and the service layer never learns which
table a number was written to. The same rule covers what leaves the module: another
module receives one of these dataclasses through ``integration_service``.
"""

from dataclasses import dataclass

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
    """

    avg_power_w: float | None = None
    avg_hr_bpm: float | None = None
    rss: float | None = None
    rss_source: str | None = None
    rss_per_min: float | None = None
    source_scores: dict[str, float] | None = None
