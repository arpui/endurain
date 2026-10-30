"""Internal contracts the analysis layers pass each other.

Values moving between service and crud are plain dataclasses, never ORM rows: the
persistence layer keeps its models to itself and the service layer never learns which
table a number was written to.
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
