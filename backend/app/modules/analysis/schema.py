"""Pydantic schemas for training load, thresholds and analysis settings."""

from collections.abc import Mapping
from datetime import date as date_type

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr

import modules.analysis.constants as analysis_constants


class ActivityLoadRead(BaseModel):
    """Serialized load of one activity, as measured by one formula.

    Attributes:
        method: Formula that produced the load.
        load: Stress score (100 = one hour held at the athlete's threshold).
        intensity_factor: Intensity the load was derived from.
        threshold_used: Threshold value the formula divided by.
        duration_s: Active time the load was computed over.
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    method: analysis_constants.LoadMethod
    load: StrictFloat
    intensity_factor: StrictFloat | None
    threshold_used: StrictFloat | None
    duration_s: StrictFloat


class PowerThresholdRead(BaseModel):
    """Serialized power threshold for one sport, with its provenance.

    Attributes:
        value_w: Threshold power in watts.
        w_prime_j: Fitted anaerobic work capacity in joules, an index rather than an
            absolute: it depends on the duration range the fit used.
        source: Derived or entered by the athlete.
        cv_pct: Fit error as a percentage of the mean, derived values only.
    """

    model_config = ConfigDict(extra="forbid")

    value_w: StrictFloat
    w_prime_j: StrictFloat | None
    source: analysis_constants.ThresholdSource
    cv_pct: StrictFloat | None


class HeartRateThresholdRead(BaseModel):
    """Serialized threshold heart rate, with its provenance."""

    model_config = ConfigDict(extra="forbid")

    value_bpm: StrictFloat
    source: analysis_constants.ThresholdSource


class AnalysisSettingsRead(BaseModel):
    """Serialized analysis parameters of one athlete.

    Attributes:
        acute_days: Fatigue time constant.
        chronic_days: Fitness time constant.
        primary_method: Formula used when a single load is asked for.
        resting_hr: Resting heart rate stated by the athlete.
        power_thresholds: Threshold per sport group, derived or manual.
        hr_threshold: Threshold heart rate, derived or manual.
        method_params: Per-formula parameters.
    """

    model_config = ConfigDict(extra="forbid")

    acute_days: StrictInt
    chronic_days: StrictInt
    primary_method: analysis_constants.LoadMethod
    resting_hr: StrictInt | None = None
    power_thresholds: dict[StrictStr, PowerThresholdRead] = Field(default_factory=dict)
    hr_threshold: HeartRateThresholdRead | None = None
    method_params: dict[StrictStr, StrictFloat] = Field(default_factory=dict)


class AnalysisSettingsUpdate(BaseModel):
    """Body updating an athlete's analysis parameters.

    Every field is optional and only what is sent changes. Sending ``null`` for a
    threshold returns that sport to the derived value instead of leaving the athlete
    with no threshold at all.

    Attributes:
        acute_days: Fatigue time constant in days.
        chronic_days: Fitness time constant in days.
        primary_method: Formula used when a single load is asked for.
        resting_hr: Resting heart rate in bpm.
        hr_threshold_bpm: Manual threshold heart rate in bpm, or ``None`` to go back
            to the derived one.
        power_threshold_w: Manual threshold power per sport group, or ``None`` per
            sport to go back to the derived one.
        method_params: Whole replacement of the per-formula parameters.
    """

    model_config = ConfigDict(extra="forbid")

    acute_days: StrictInt | None = Field(default=None, ge=1, le=180)
    chronic_days: StrictInt | None = Field(default=None, ge=1, le=365)
    primary_method: analysis_constants.LoadMethod | None = None
    resting_hr: StrictInt | None = Field(default=None, ge=20, le=120)
    hr_threshold_bpm: StrictFloat | None = Field(default=None, ge=60, le=230)
    power_threshold_w: Mapping[StrictStr, StrictFloat | None] | None = None
    method_params: Mapping[StrictStr, StrictFloat] | None = None


class FitnessPointRead(BaseModel):
    """Serialized one-day point of the fitness/fatigue series.

    Attributes:
        date: The calendar day (the athlete's local day the activity started on).
        load: Summed load of that day, in the shared scale.
        acute: Fatigue: the exponentially weighted load with the acute time constant.
        chronic: Fitness: the same with the chronic time constant.
        balance: Form minus fatigue (fitness - fatigue), positive means fresh.
    """

    model_config = ConfigDict(extra="forbid")

    date: date_type
    load: StrictFloat
    acute: StrictFloat
    chronic: StrictFloat
    balance: StrictFloat


class FitnessSeriesRead(BaseModel):
    """Serialized daily-load series with its parameters, so a chart can label itself.

    Attributes:
        method: Which formula's rows were summed.
        acute_days: Fatigue time constant used.
        chronic_days: Fitness time constant used.
        sport: Sport group the series is limited to, ``None`` for all sports.
        points: One point per consecutive day of the requested range.
    """

    model_config = ConfigDict(extra="forbid")

    method: analysis_constants.LoadMethod
    acute_days: StrictInt
    chronic_days: StrictInt
    sport: StrictStr | None
    points: list[FitnessPointRead]


class RecomputeQueued(BaseModel):
    """Response of a recompute request.

    Attributes:
        queued: Always ``True``: the pass runs off the request path.
    """

    model_config = ConfigDict(extra="forbid")

    queued: bool
