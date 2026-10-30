"""The load formulas the analysis module can compute, kept as a registry.

Every formula answers the same question -- how much stress did this activity put on
this athlete -- and every one except :meth:`LoadMethod.TRIMP` answers it on the same
scale: **100 units is one hour held at the athlete's threshold**
(``100 · hours · intensity²``). Holding that scale is what lets a day's load add up
across running, hiking and cycling, and what makes a fitness/fatigue series built on
top of it mean anything.

An athlete's own formula joins :data:`LOAD_FORMULAS` with its calculator and nothing
else in the module changes: it receives the activity's samples and thresholds through
:data:`LoadInput`, its knobs arrive in
:attr:`LoadInput.parameters` from the athlete's settings, and its result is stored in
one :class:`ActivityLoad` row like any other.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from math import exp

import modules.analysis.constants as analysis_constants
import modules.analysis.maths as analysis_maths


@dataclass(frozen=True)
class LoadInput:
    """What a formula is given about one activity.

    Attributes:
        moving_seconds: Active time of the activity, the duration the load scales with.
        power_w: Power samples in watts in stream order (stops kept as zero), or
            ``None`` when the activity carries no power.
        period_s: Seconds between power samples.
        stored_np_w: Normalized Power as the ingestion stored it, preferred over
            recomputing it when present.
        average_hr: Average heart rate of the activity, or ``None``.
        max_hr: Highest heart rate seen in the activity, the athlete's value when the
            activity has none.
        power_threshold_w: Threshold power for the activity's sport, or ``None``.
        hr_threshold_bpm: Threshold heart rate, or ``None``.
        resting_hr_bpm: Resting heart rate, or ``None`` when unknown.
        parameters: Formula-specific knobs from the athlete's settings.
    """

    moving_seconds: float
    power_w: tuple[float, ...] | None = None
    period_s: float = 1.0
    stored_np_w: float | None = None
    average_hr: float | None = None
    max_hr: float | None = None
    hr_samples: tuple[float, ...] = ()
    power_threshold_w: float | None = None
    hr_threshold_bpm: float | None = None
    resting_hr_bpm: float | None = None
    parameters: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class LoadValue:
    """What a formula returns: the load plus what it was measured against.

    Attributes:
        load: Stress in the shared scale (100 = one hour at threshold).
        intensity_factor: Intensity the load was derived from, when the formula has one.
        threshold_used: Threshold value the formula divided by, so a later change of
            threshold never silently reinterprets a stored row.
    """

    load: float
    intensity_factor: float | None = None
    threshold_used: float | None = None


def stress_from_intensity(intensity_factor: float, seconds: float) -> float:
    """Return the shared-scale load for an intensity factor held for ``seconds``.

    ``100 · hours · IF²``: one hour at threshold is 100, two hours at threshold is
    200, one hour at 80 % of threshold is 64.
    """
    return 100.0 * (seconds / 3600.0) * intensity_factor**2


def _normalized_power_w(data: LoadInput) -> float:
    """Return the activity's Normalized Power, preferring what ingestion stored."""
    if data.stored_np_w and data.stored_np_w > 0:
        return float(data.stored_np_w)
    if not data.power_w:
        return 0.0
    return analysis_maths.normalized_power(data.power_w, data.period_s)


def coggan_tss(data: LoadInput) -> LoadValue | None:
    """Training Stress Score: Normalized Power over the threshold power.

    Returns ``None`` when either number is missing, so a power formula never
    guesses a threshold.
    """
    threshold = data.power_threshold_w
    normalized = _normalized_power_w(data)
    if not threshold or threshold <= 0 or not normalized or data.moving_seconds <= 0:
        return None
    intensity = normalized / threshold
    return LoadValue(
        load=stress_from_intensity(intensity, data.moving_seconds),
        intensity_factor=intensity,
        threshold_used=float(threshold),
    )


def skiba_score(data: LoadInput) -> LoadValue | None:
    """Stress score from xPower, which reacts sooner to bursts than Normalized Power.

    Note on running power sampled at one second: the attenuated average this uses
    smooths *more* than the 30-second boxcar, so on that data it scores slightly
    below the Coggan score rather than above it. It is kept because it is the
    standard alternative for variable cycling efforts, not as the anaerobic
    indicator.
    """
    threshold = data.power_threshold_w
    if not threshold or threshold <= 0 or not data.power_w or data.moving_seconds <= 0:
        return None
    burst_weighted = analysis_maths.xpower(data.power_w, data.period_s)
    if not burst_weighted:
        return None
    intensity = burst_weighted / threshold
    return LoadValue(
        load=stress_from_intensity(intensity, data.moving_seconds),
        intensity_factor=intensity,
        threshold_used=float(threshold),
    )


def hr_load(data: LoadInput) -> LoadValue | None:
    """Load from average heart rate over the threshold heart rate.

    The same scale as the power formulas, which is what lets an activity with no
    power still add to the athlete's overall load.
    """
    threshold = data.hr_threshold_bpm
    if not threshold or threshold <= 0 or not data.average_hr or data.moving_seconds <= 0:
        return None
    intensity = data.average_hr / threshold
    return LoadValue(
        load=stress_from_intensity(intensity, data.moving_seconds),
        intensity_factor=intensity,
        threshold_used=float(threshold),
    )


def trimp(data: LoadInput) -> LoadValue | None:
    """Training impulse from heart-rate reserve (Banister).

    A different scale from the stress formulas, so it is reported beside them and
    never summed with them. It needs a resting heart rate; without one it returns
    ``None`` rather than assuming a value the athlete never gave.
    """
    max_hr, resting = data.max_hr, data.resting_hr_bpm
    if not data.average_hr or not max_hr or not resting or max_hr <= resting or data.moving_seconds <= 0:
        return None
    reserve = (data.average_hr - resting) / (max_hr - resting)
    if reserve <= 0:
        return None
    coefficient = float(data.parameters.get("trimp_coefficient", 1.92))
    load = (data.moving_seconds / 60.0) * reserve * 0.64 * exp(coefficient * reserve)
    return LoadValue(load=load, intensity_factor=reserve, threshold_used=float(resting))


@dataclass(frozen=True)
class LoadFormula:
    """One entry of the registry.

    Attributes:
        method: Stored in ``analysis_activity_loads.method``; part of the row's identity.
        label: Human-readable name, for the settings screen and tooltips.
        calculator: The function computing the load, or ``None`` when the activity
            lacks what the formula needs.
    """

    method: analysis_constants.LoadMethod
    label: str
    calculator: Callable[[LoadInput], LoadValue | None]


#: Every formula the module computes, in the order they are stored and listed.
#: Adding a formula means adding a calculator above and one entry here.
LOAD_FORMULAS: tuple[LoadFormula, ...] = (
    LoadFormula(analysis_constants.LoadMethod.COGGAN_TSS, "Training Stress Score", coggan_tss),
    LoadFormula(analysis_constants.LoadMethod.SKIBA_SCORE, "Stress score (xPower)", skiba_score),
    LoadFormula(analysis_constants.LoadMethod.HR_LOAD, "Heart-rate load", hr_load),
    LoadFormula(analysis_constants.LoadMethod.TRIMP, "Training impulse (TRIMP)", trimp),
)


def formula_for(method: analysis_constants.LoadMethod | str) -> LoadFormula | None:
    """Return the registry entry for a method, or ``None`` when it is unknown."""
    for entry in LOAD_FORMULAS:
        if entry.method == method:
            return entry
    return None


def compute_all(data: LoadInput) -> list[tuple[analysis_constants.LoadMethod, LoadValue]]:
    """Run every registered formula and keep the ones this activity supports.

    Returns:
        ``(method, value)`` for each formula the activity's data allowed, in registry
        order; empty when the activity carries nothing any formula can read.
    """
    results: list[tuple[analysis_constants.LoadMethod, LoadValue]] = []
    for entry in LOAD_FORMULAS:
        value = entry.calculator(data)
        if value is not None:
            results.append((entry.method, value))
    return results
