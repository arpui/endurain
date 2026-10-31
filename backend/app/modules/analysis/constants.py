"""Domain constants for the analysis module.

The vocabulary (which formulas exist, where a threshold came from) and the tuning
numbers (smoothing windows, fit ranges, default time constants) are stated once,
here: ``models`` stamps the defaults and the check constraints, ``crud`` filters on
the values, ``service`` compares against them, ``methods`` reads the windows and
``schema`` types the fields clients see.
"""

from enum import StrEnum

#: Default time constants of the fitness/fatigue series (days). Kept as
#: parameters of the athlete, not constants of the code: these are the defaults a
#: settings row starts from, matching the 7/42 convention the impulse-response
#: model uses.
ACUTE_DAYS_DEFAULT = 7
CHRONIC_DAYS_DEFAULT = 42

#: Load formula used when a caller asks for "the" load of an activity without
#: naming a method.
PRIMARY_METHOD_DEFAULT = "coggan_tss"

#: Smoothing window of Normalized Power, in seconds. GoldenCheetah averages the
#: power over this window before taking fourth powers (``Coggan.cpp:62-110``);
#: the value that reproduces what devices and Strava store for these activities is
#: the same 30 s, with stops counted as zero power.
NP_WINDOW_S = 30.0

#: Averaging constant of xPower, in seconds (``BikeScore.cpp:60-110``). A shorter
#: constant than NP's window, so bursts raise it sooner.
XPOWER_MEAN_S = 25.0

#: Window used to derive a threshold heart rate from the athlete's own records:
#: the best sustained 30-minute average, the running analogue of a 30-minute
#: power test.
LTHR_WINDOW_S = 1800.0

#: Durations (seconds) whose best sustained average is stored for the
#: duration-intensity curve. A deliberate ladder, not every second: it covers the
#: sprint-to-endurance range the models are read over, and keeps one row per
#: athlete, sport and duration.
DURATION_PEAKS_S: tuple[int, ...] = (
    5,
    10,
    15,
    30,
    60,
    120,
    300,
    420,
    600,
    780,
    900,
    1200,
    1500,
    1800,
    2400,
    3000,
    3600,
)

#: Lower bound of the duration range used to fit critical power. Short efforts are
#: excluded on purpose: an extrapolation from seconds to hours through them lands
#: above what the athlete can actually hold (a fit over 2-20 min gave 378 W where
#: the best 60-minute average was 338 W).
FIT_MIN_S = 300.0

#: Upper bound of the critical-power fit range, in seconds.
FIT_MAX_S = 1800.0

#: Peak durations the fit needs before its slope means anything.
FIT_MIN_PEAKS = 4

#: Activities that must contribute peaks before a derived threshold is trusted.
#: One activity alone fits a curve through its own samples only, which invented a
#: 202 W threshold from a single walk.
FIT_MIN_ACTIVITIES = 3

#: Days a fitness-series request covers when the caller names no range: one typical
#: mesocycle, with the decay still seeded from the athlete's first measured day.
FITNESS_WINDOW_DEFAULT_DAYS = 90

#: Activities offered on the power-duration response as W' balance candidates,
#: newest first: enough to pick from without shipping the whole history.
POWER_DURATION_SESSIONS_MAX = 50

#: Upper bound of points transferred for one W' balance series. Charts do not
#: render one point per second of a multi-hour activity, and the JSON should not
#: carry them.
WPRIME_POINTS_MAX = 600

#: Longest gap (seconds) still treated as continuous sampling when deriving the
#: stream's sample period. A larger median gap means the device recorded sparsely
#: and the windows widen to match.
MAX_SAMPLE_PERIOD_S = 10.0


class LoadMethod(StrEnum):
    """How one activity's training load was measured.

    Every method except :attr:`TRIMP` answers on the same scale: 100 units is one
    hour held at the athlete's threshold. That shared scale is what lets a day's
    load add up across sports and methods, and what makes an impulse-response
    series meaningful. ``TRIMP`` is a different scale (Banister's) and is reported
    beside the others, never added to them.

    Attributes:
        COGGAN_TSS: Training Stress Score from Normalized Power over the threshold.
        SKIBA_SCORE: Stress score from xPower, which reacts sooner to bursts.
        HR_LOAD: Load from average heart rate over the threshold heart rate.
        TRIMP: Training impulse from heart-rate reserve (reported, not summed).
    """

    COGGAN_TSS = "coggan_tss"
    SKIBA_SCORE = "skiba_score"
    HR_LOAD = "hr_load"
    TRIMP = "trimp"


class ThresholdSource(StrEnum):
    """Where a threshold a load formula used came from.

    Attributes:
        AUTO: Derived from the athlete's own records (peak-power fit, best sustained
            heart rate); refreshed by the threshold pass and never overwrites a
            manual value.
        MANUAL: Entered by the athlete; wins over anything derived.
    """

    AUTO = "auto"
    MANUAL = "manual"
