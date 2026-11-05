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

#: A critical power that evolves: the threshold used to score an activity is the
#: best *decayed* per-session fit around its date, not the all-time peak curve.
#: Evidence older than the day relaxes at ``DECAY_PER_DAY`` (≈9% over a quarter
#: of no hard efforts — detraining in the literature); evidence up to FWD_DAYS
#: ahead still counts undecayed (it is known at scoring time). A window with
#: fewer than MIN_FITS usable fits falls back to the stored threshold.
TEMPORAL_CP_BACK_DAYS = 90
TEMPORAL_CP_FWD_DAYS = 45
TEMPORAL_CP_DECAY_PER_DAY = 0.003
TEMPORAL_CP_MIN_FITS = 4

#: A per-session fit counts as evidence only when its regression is tight: the
#: coefficient of variation must stay under this percent...
TEMPORAL_CP_MAX_CV_PCT = 10.0

#: ...and the fitted ceiling cannot exceed the session's own best sustained window
#: average by more than this factor (a two-parameter fit loves to overshoot the
#: last real data point; the session itself is the bound on what it held).
TEMPORAL_CP_CEIL_FACTOR = 1.15

#: Method parameter that switches the temporal power threshold off per athlete
#: (stored in ``method_params``; on by default). Manual thresholds are always
#: respected regardless.
TEMPORAL_CP_PARAM = "temporal_cp"

#: Peak plausibility for the duration-intensity cache. A session's best average
#: for a duration is refused (and the whole session skipped, so the same corrupt
#: sensor cannot poison neighbouring durations) when it exceeds BOTH the tier
#: ratio over the session's own normalized power AND an absolute floor. The
#: ratios come from the observed distribution of plausible sessions (p99: 1.86
#: at 1 min, 1.64 at 2 min, 1.50 at 5 min); the floors keep surges on low-NP
#: days (walks) from being flagged. The tiers: ``(max_duration_s, ratio_cap)``.
#: Durations under 30 s are not judged at all: literature puts short sprint
#: peaks (50-80 W/kg mechanical for seconds, and Stryd reports what the legs
#: produce, not what the ground demands) far above anything an absolute watt
#: ceiling could separate from honest effort - those tails are judged by their
#: speed/grade context, not by watts.
PEAK_OUTLIER_MIN_S = 30.0
PEAK_OUTLIER_TIERS: tuple[tuple[float, float], ...] = (
    (60.0, 2.6),
    (180.0, 1.9),
    (600.0, 1.7),
    (3600.0, 1.45),
)

#: Absolute floors per sport group, in watts: sustained by whom for how long is
#: no longer a human reading (run: world-class 1 min sits near 500 W even with
#: pod inflation; ride: 1 min above a kilowatt is a track sprinter).
PEAK_OUTLIER_FLOOR_W: dict[str, float] = {"run": 800.0, "bike": 1000.0}
PEAK_OUTLIER_FLOOR_W_DEFAULT = 800.0

#: Rolling windows, in months back from today, whose duration-intensity curves are
#: overlaid on the full-history one: recent form read against the athlete's record.
PD_OVERLAY_MONTHS: tuple[int, ...] = (3, 6)

#: Canonical race distances whose best times are tabulated, in metres. The half
#: marathon is the exact 21,097 m, not a round number.
BT_DISTANCES_M: tuple[int, ...] = (
    100,
    200,
    300,
    400,
    500,
    600,
    800,
    1000,
    2000,
    3000,
    4000,
    5000,
    10000,
    15000,
    20000,
    21097,
    30000,
)

#: Net grade a split may not fall below, so a downhill sprint is not listed as a
#: personal best. GPS altitude noise dominates short splits, so below this length
#: the looser bound applies; from it up, a real descent is separable from the noise.
BT_MIN_GRADE_PCT = -1.0
BT_SHORT_MIN_M = 800
BT_MIN_GRADE_PCT_SHORT = -2.0

#: Split average speed ceiling (m/s), above the 100 m world record pace: GPS jitter
#: briefly "covers" a distance in an impossible time, and no wattage backs it up.
BT_MAX_SPEED_MS = 8.0

#: Mean power a short split must hold, as a multiple of the athlete's threshold, for
#: its speed to count as fitness: under 800 m GPS speed alone is too easy to fake by
#: gravity or jitter, so the engine has to be there (the interval quality gate's idea).
BT_EFFORT_IF = 1.05

#: Activities that must contribute peaks before a derived threshold is trusted.
#: One activity alone fits a curve through its own samples only, which invented a
#: 202 W threshold from a single walk.
FIT_MIN_ACTIVITIES = 3

#: Days a fitness-series request covers when the caller names no range: one typical
#: mesocycle, with the decay still seeded from the athlete's first measured day.
FITNESS_WINDOW_DEFAULT_DAYS = 90

#: A power stream whose highest sample sits below this is treated as absent by the
#: analysis module: a pod-less session records as a column of zeros (Garmin writes
#: the channel even when Stryd is off), and letting it score zero watts would
#: hijack the intensity chain from the pace/HR sources that do have signal. No
#: real effort peaks below this floor, so max-over-stream separates cleanly.
POWER_NO_SIGNAL_MAX_W: float = 30.0

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

#: Window the recorded grade is median-filtered over, in seconds. GPS elevation
#: carries a few metres of noise, which over a few seconds of ground reads as a
#: steep hill where the athlete was flat out.
GRADE_MEDIAN_WINDOW_S = 15.0

# --- Interval analysis of one activity -----------------------------------------
#
# The activity's own timeline is split into effort segments. The defaults below are
# tunable per athlete through ``analysis_settings.method_params`` (keys named here),
# because how hard a rep has to be, and how short it can be, changes with the athlete
# and the discipline. Two anchors are used together on purpose: the athlete's
# threshold (what the effort means physiologically) and the session's own median
# power (what was hard *that day*), which is what tells a 4 km surge in a 10 km race
# apart from a race run all the way through.

#: Samples are median-filtered over this window before segmentation, so a handful of
#: noisy seconds do not open and close a segment.
IV_SMOOTH_WINDOW_S = 12.0

#: A segment starts at ``IV_ENTER_CP · threshold`` and at least ``IV_ENTER_BASE ·``
#: the session median, and ends when power falls to ``IV_EXIT_CP · threshold`` or
#: ``IV_EXIT_BASE ·`` the median, whichever is higher.
IV_ENTER_CP = 1.00
IV_ENTER_BASE = 1.15
IV_EXIT_CP = 0.90
IV_EXIT_BASE = 1.02

#: Shortest run (seconds) kept as a segment; anything briefer is a stride.
IV_MIN_SEGMENT_S = 20.0

#: A segment shorter than this is only counted when it stands much further above the
#: session median, so brief surges inside an easy run are not called intervals.
IV_SHORT_SEGMENT_S = 60.0
IV_SHORT_BASE = 1.30

#: Above the session median by this much, a segment counts on its own even without a
#: recovery in front of it (a race's opening surge, a solo tempo push).
IV_STRONG_BASE = 1.22

#: The gap between two segments counts as a real recovery when its average stays at
#: or below this fraction of the threshold.
IV_RECOVERY_CP = 0.80

#: A sustained stretch held inside the threshold band (between ``IV_EXIT_CP`` and
#: ``IV_ENTER_CP`` times the threshold) for at least this many seconds is a TEMPO
#: effort: a threshold block that never broke the ceiling but is no recovery either.
IV_TEMPO_MIN_S = 120.0

#: A tempo stretch closes when power falls to this fraction of the threshold: a hair
#: under the opening gate, the same hysteresis that keeps an interval whole.
IV_TEMPO_EXIT_CP = 0.88

#: A gap no longer than this (seconds), or this share of the effort before it, is the
#: same effort interrupted by a wobble rather than a rest between two.
IV_MERGE_GAP_S = 60.0
IV_MERGE_SHARE = 0.25

#: A segment is a climb (terrain, not a chosen rep) when its mean grade is above this
#: percent and its intensity stays below ``IV_CLIMB_IF``.
IV_CLIMB_GRADE_PCT = 3.5
IV_CLIMB_IF = 1.25

#: A long segment covering this share of the measured time is the session's steady
#: effort (a race, a tempo run), not one interval among several.
IV_STEADY_SHARE = 0.50

#: Intensity below which that session-long effort reads as steady rather than as one
#: long rep: holding 1,2x threshold for half an hour is an interval, holding 0,95x is
#: a tempo or a race.
IV_STEADY_IF = 1.15

#: A flat effort must hold at least this multiple of the threshold on average to count
#: as an interval: brief contact with the gate is not a rep, sustained work above it is.
#: Grade-driven pushes are exempt; the climb rule already weighs what the terrain justifies.
IV_EFFORT_IF = 1.05

#: Median window (seconds) for the elevation a segment reports: raw GPS altitude
#: jitters a couple of metres per sample, and a plain sum of deltas would read that
#: noise as climbing.
IV_ELEV_MEDIAN_S = 30.0

# --- Grade-adjusted running pace (critical speed) ---------------------------
# The GoldenCheetah 3.8 grade-adjusted-pace model: a degree-5 polynomial fitted
# to Strava's GAP curve ("An improved GAP model", Strava Engineering), close to
# Minetti's 2002 measurements but with a lower factor uphill and a much lower one
# downhill: descending does not repay what climbing costs. A factor multiplies
# the actual speed to give the equivalent flat-ground speed, so a slow steep
# climb reads as the hard effort it was.

#: Beyond this |grade|, in percent, the polynomial is replaced by the constants
#: GoldenCheetah uses at its edge of validity.
GAP_SLOPE_CLAMP_PCT = 40.0

#: Factor for any grade above the clamp (the polynomial's value at +40 %).
GAP_CLAMP_FACTOR_UP = 3.739

#: Factor for any grade below the negative clamp.
GAP_CLAMP_FACTOR_DOWN = 1.937

#: Polynomial coefficients, highest power first, over the grade in percent.
GAP_COEFFICIENTS: tuple[float, ...] = (
    -0.00000000340028981678083,
    -0.000000471345135734814,
    +0.000000777801576537029,
    +0.001906977,
    +0.0299822939,
    +0.9935756154,
)

#: The ceiling pace-side scoring believes for one second's flat-ground-equivalent
#: pace, as a multiple of the critical speed. A runner's genuinely hardest second
#: sits near two times critical; above this the input is GPS or barometric noise
#: (the grade garbage GAP then compounds), and an exponential or fourth-power
#: kernel would spend it like a superhuman surge.
PACE_INTENSITY_CEILING = 2.5

#: The window pace-side scoring averages adjusted pace over, in seconds. GPS speed
#: cannot prove anything about one second of effort: its jitter, amplified by a
#: convex kernel, inflates whole activities. What pace can resolve is tens of
#: seconds, so that is the resolution pace scores at.
PACE_SMOOTH_WINDOW_S = 20.0

#: Grade clip (percent) for the calibration's grade term: what the steady-state
#: GAP model under-reads grows with grade to ~8-10 % and flips past that, so the
#: correction saturates at this band edge rather than extrapolating steep trails.
PACE_CAL_GRADE_CAP_PCT = 6.0

#: Minimum paired samples (seconds) before a pace-to-watts calibration is trusted.
PACE_CAL_MIN_SECONDS = 1800

#: Minimum squared correlation before a pace-to-watts calibration is stored; below
#: it the athlete's pace explains too little of their power to be worth the map.
PACE_CAL_MIN_R2 = 0.4

#: A flat grade gets a factor of exactly 1; the polynomial's value at zero is a
#: hair under it, and flat ground should not be adjusted at all.
GAP_FLAT_CORRECTION_PCT = 0.0

# --- Terrain climbs -------------------------------------------------------------
# A climb the route itself shows, independent of how the athlete rode or ran it:
# the smoothed altitude profile, not the power signal, decides. The numbers below
# are defaults only: every one of them is an ``iv_terrain_*`` athlete override.

#: Window (metres of ground) the open/close grade is measured over.
IV_TERRAIN_WINDOW_M = 150.0

#: Net grade over the window that starts a climb.
IV_TERRAIN_OPEN_PCT = 2.5

#: Below this net grade the climb is over; the gap between open and close is the
#: hysteresis that keeps a steady climb in one piece.
IV_TERRAIN_CLOSE_PCT = 0.5

#: Shortest stretch still worth listing.
IV_TERRAIN_MIN_LENGTH_M = 250.0

#: Fewest metres climbed worth listing.
IV_TERRAIN_MIN_GAIN_M = 20.0

#: A stretch whose whole-profile grade stays below this is rolling ground, not a
#: climb: it gained as much as it let go. Half the opening grade is enough to tell
#: a climb with real dips apart from a rolling ridge that merely trends up.
IV_TERRAIN_MIN_NET_PCT = 1.5

#: A dip shorter than this (metres of ground) between two climbs does not end the
#: first one.
IV_TERRAIN_MERGE_GAP_M = 600.0

#: A dip up to this deep (metres) is a false flat inside a climb, not a break.
IV_TERRAIN_MERGE_DIP_M = 30.0

#: Or a dip up to this share of what the climb had already gained.
IV_TERRAIN_MERGE_DIP_FRAC = 0.35

#: Minimum segments for the session to read as interval work rather than a single
#: push.
IV_MIN_REPS = 2

#: With no segment standing out, a session whose own median already sits this high
#: over the threshold still reads as one sustained effort (a race, a tempo run)
#: rather than an easy day.
IV_CONTINUOUS_BASE = 0.85

#: Spread (std/mean of durations) under which reps read as a planned set.
IV_REGULAR_CV = 0.55


class LoadMethod(StrEnum):
    """How one activity's training load was measured.

    Every method except :attr:`TRIMP` answers on the same scale: 100 units is one
    hour held at the athlete's threshold. That shared scale is what lets a day's
    load add up across sports and methods, and what makes an impulse-response
    series meaningful. ``TRIMP`` is a different scale (Banister's) and is reported
    beside the others, never added to them.

    :attr:`DANIELS` scores effort the way Jack Daniels' pace tables do: one
    second at intensity ``x`` over threshold is worth ``x⁴``, normalized so an
    hour at threshold is 100 points. That fourth power makes it a scale of its
    own like ``TRIMP``: it answers beside the others and is never added to them.

    The ``RSS_*`` methods are one family: a stress kernel that grows exponentially
    with intensity up to a knee and linearly past it, summed per second over the
    samples of an activity that carries power. :attr:`RSS_TOTAL`
    is the family's general load; the other four split it by *where* the effort
    happened (climbs, high, middle and low intensity), each sample falling in
    exactly one of them. They answer on the shared scale too: the kernel is
    normalized so one hour at threshold is ~100.

    Attributes:
        COGGAN_TSS: Training Stress Score from Normalized Power over the threshold.
        SKIBA_SCORE: Stress score from xPower, which reacts sooner to bursts.
        HR_LOAD: Load from average heart rate over the threshold heart rate.
        TRIMP: Training impulse from heart-rate reserve (reported, not summed).
        RSS_TOTAL: Exponential-intensity stress over every powered second.
        RSS_UP: The share of that stress taken on climbs (grade above a cutoff).
        RSS_HIGH: The share taken above a high fraction of threshold, off climbs.
        RSS_MID: The share taken between the band cutoffs, off climbs.
        RSS_LOW: The share taken below a low fraction of threshold, slow and off
            climbs.
        DANIELS: Daniels points, intensity to the fourth power (reported,
            not summed).
    """

    COGGAN_TSS = "coggan_tss"
    SKIBA_SCORE = "skiba_score"
    HR_LOAD = "hr_load"
    TRIMP = "trimp"
    RSS_TOTAL = "rss_total"
    RSS_UP = "rss_up"
    RSS_HIGH = "rss_high"
    RSS_MID = "rss_mid"
    RSS_LOW = "rss_low"
    DANIELS = "daniels_points"


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


#: Every stored method, as a SQL ``IN`` list, so the two check constraints and the
#: registry can never drift apart from the enum.
def method_sql_list() -> str:
    """Return the quoted ``IN (...)`` list of every :class:`LoadMethod` value."""
    return ", ".join(f"'{method.value}'" for method in LoadMethod)


# --- Exponential (RSS) stress family -------------------------------------------
#
# The athlete's own formula, fitted on running power: each second of an activity
# that carries power contributes a stress rate that grows exponentially with the
# intensity up to a knee, then keeps growing linearly, and the activity's load is
# that rate summed over the seconds. The
# defaults below are the fitted constants; every one of them is overridable per
# athlete through ``analysis_settings.method_params``, keyed as named here.

#: Intensity (power over threshold) where the kernel's exponential stops and a
#: straight line takes over, carrying the curve's value and slope at that point.
#: The fit was made in an era whose efforts rarely reached this far above threshold,
#: so past it the unbounded exponential is extrapolation: a capped-linear kernel
#: keeps going all-out visible as extra cost without multiplying it per second.
#: An athlete's ``method_params`` key ``rss_kernel_cap_if`` moves the knee; 0 (or
#: negative) removes it and restores the historical unbounded curve for reviews.
RSS_KERNEL_CAP_IF = 1.5

#: Exponential coefficient ``A`` of the kernel ``B + A · e^(E · power/threshold)``,
#: up to the knee above.
RSS_KERNEL_A = 0.0289929

#: Exponent coefficient ``E`` of the kernel.
RSS_KERNEL_E = 3.82932

#: Constant term ``B`` of the kernel.
RSS_KERNEL_B = 0.363086

#: Sum divisor: the kernel value one hour at threshold is normalized to, which puts
#: the family on the shared scale (100 = one hour at threshold).
RSS_NORM = 1.6


#: Grade (%) above which a second is a climb: it is measured by ``RSS_UP`` and
#: never by the intensity bands, and the kernel gets its steepest floor.
RSS_CLIMB_GRADE_PCT = 10.0

#: Grade (%) above which the kernel gets its first floor: a gradient costs more
#: than its watts alone.
RSS_SOFT_GRADE_PCT = 5.0

#: Kernel floor applied above the soft grade, and above the climb grade.
RSS_SOFT_FLOOR = 0.65
RSS_CLIMB_FLOOR = 0.8

#: Band edges, as fractions of the threshold power. A second is low at or below
#: ``RSS_LOW_FRACTION`` while moving slowly, high above ``RSS_HIGH_FRACTION``, and
#: middle in between (a fast low-power second counts as middle, not low).
RSS_LOW_FRACTION = 0.5
RSS_HIGH_FRACTION = 0.95

#: Moving speed (m/s) below which a low-power second is a slow one.
RSS_SLOW_SPEED_MS = 5.0

#: Sub-thirty-second context rule (a sensor runaway, not a sprint): a short
#: average above MIN_W held while the athlete moves below MAX_V_MS, with every
#: neighboring 30 s block under NEIGHBOR_SHARE of it - an honest steep climb is
#: slow too, but it is surrounded by the same effort.
PEAK_RUNAWAY_MIN_W = 900.0
PEAK_RUNAWAY_MAX_V_MS = 3.0
PEAK_RUNAWAY_NEIGHBOR_SHARE = 0.7
