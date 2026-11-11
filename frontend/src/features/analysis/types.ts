/**
 * Domain models for training-load analysis. DTO aliases derive from the
 * generated client so a backend contract change surfaces as a compile error in
 * the service mappers.
 */

import type { GeoPoint } from '@/composables/useMapProvider'
import type { Schemas } from '@/types'

export type AnalysisSettingsDto = Schemas['AnalysisSettingsRead']
export type AnalysisSettingsUpdateDto = Schemas['AnalysisSettingsUpdate']
export type FitnessSeriesDto = Schemas['FitnessSeriesRead']
export type FitnessPointDto = Schemas['FitnessPointRead']
export type PowerThresholdDto = Schemas['PowerThresholdRead']
export type HeartRateThresholdDto = Schemas['HeartRateThresholdRead']
export type PowerDurationDto = Schemas['PowerDurationRead']
export type DurationPeakDto = Schemas['DurationPeakRead']
export type PowerDurationFitDto = Schemas['PowerDurationFitRead']
export type PowerDurationOverlayDto = Schemas['PowerDurationOverlayRead']
export type PowerDurationSessionDto = Schemas['PowerDurationSessionRead']
export type WprimeBalanceDto = Schemas['WprimeBalanceRead']
export type WprimePointDto = Schemas['WprimePointRead']
export type CpTrendDto = Schemas['CpTrendRead']
export type PowerFlagDto = Schemas['PowerFlagRead']

export type CpTrendPointDto = Schemas['CpTrendPointRead']

/** Load formula keys, mirroring the backend `LoadMethod` enum. */
export type LoadMethod =
  | 'coggan_tss'
  | 'skiba_score'
  | 'hr_load'
  | 'trimp'
  | 'daniels_points'
  | 'rss_total'
  | 'rss_up'
  | 'rss_high'
  | 'rss_mid'
  | 'rss_low'

/** How a stored threshold was obtained: derived from records or typed in. */
export type ThresholdSource = 'auto' | 'manual'

/** Power threshold of one sport group, with its provenance. */
export interface PowerThreshold {
  /** Threshold power in watts (critical power). */
  valueW: number
  /** Fitted anaerobic work capacity in joules; an index, not an absolute. */
  wPrimeJ: number | null
  /** Derived or entered by the athlete. */
  source: ThresholdSource
  /** Fit error as a percentage of the mean, derived values only. */
  cvPct: number | null
}

/** Threshold heart rate, with its provenance. */
/** The pace-side twin of the power threshold: critical speed, per sport. */
export interface SpeedThreshold {
  /** Critical speed in metres per second. */
  valueMs: number
  /** Distance capacity above critical, in metres; an index, not an absolute. */
  wPrimeM: number | null
  /** Derived or entered by the athlete. */
  source: ThresholdSource
  /** Fit error as a percentage of the mean, derived values only. */
  cvPct: number | null
}

/** The athlete's own pace-to-watts map, fitted over paired power/GPS runs. */
export interface PaceCalibration {
  /** Watts gained per metre/second of grade-adjusted pace. */
  slopeWPerMs: number
  /** Fit artefact at zero speed; only the line over the athlete's real range matters. */
  interceptW: number
  /** Squared correlation over the paired samples. */
  r2: number | null
  /** Watts added per (m/s x grade %) off flat ground; 0 means the plain line. */
  gradeWPerMsPct: number
  /** Derived from records (manual is reserved). */
  source: ThresholdSource
}

export interface HeartRateThreshold {
  /** Threshold heart rate in bpm. */
  valueBpm: number
  /** Derived or entered by the athlete. */
  source: ThresholdSource
}

/** One athlete's analysis parameters as the UI consumes them. */
export interface AnalysisSettings {
  /** Fatigue time constant in days. */
  acuteDays: number
  /** Fitness time constant in days. */
  chronicDays: number
  /** Formula used when a single load is asked for. */
  primaryMethod: LoadMethod
  /** Shortest peak duration for the CP fit; `null` keeps the server default. */
  fitFromS: number | null
  /** Longest peak duration for the CP fit; `null` keeps the server default. */
  fitToS: number | null
  /** Resting heart rate stated by the athlete. */
  restingHr: number | null
  /** Power threshold per sport group. */
  powerThresholds: Record<string, PowerThreshold>
  /** Threshold running speeds by sport, when derived or stated. */
  speedThresholds: Record<string, SpeedThreshold>
  /** Pace-to-watts maps by sport, when their records pair both signals. */
  paceCalibrations: Record<string, PaceCalibration>
  /** Threshold heart rate, when known. */
  hrThreshold: HeartRateThreshold | null
  /** Per-formula parameters keyed by method. */
  methodParams: Record<string, number>
}

/** One day of the fitness/fatigue series. */
export interface FitnessPoint {
  /** ISO calendar date of the day. */
  date: string
  /** Summed load of that day in the shared scale. */
  load: number
  /** Fatigue: exponentially weighted load, acute constant. */
  acute: number
  /** Fitness: exponentially weighted load, chronic constant. */
  chronic: number
  /** Form: fitness minus fatigue; positive means fresh. */
  balance: number
}

/** Daily load series with the parameters that produced it. */
export interface FitnessSeries {
  /** Which formula's rows were summed. */
  method: LoadMethod
  /** Fatigue time constant used. */
  acuteDays: number
  /** Fitness time constant used. */
  chronicDays: number
  /** Sport group the series is limited to, `null` for all sports. */
  sport: string | null
  /** One point per consecutive day. */
  points: FitnessPoint[]
}

/** Query parameters of one fitness-series request. */
export interface FitnessQuery {
  /** Formula to sum; the stored primary when omitted. */
  method?: LoadMethod
  /** Fatigue time constant override, in days. */
  acuteDays?: number
  /** Fitness time constant override, in days. */
  chronicDays?: number
  /** Sport group to restrict the series to. */
  sport?: string
  /** First ISO date of the window. */
  fromDate?: string
  /** Last ISO date of the window. */
  toDate?: string
}

/** One point of the duration-intensity curve. */
export interface DurationPeak {
  /** Duration the best average was held for, in seconds. */
  durationS: number
  /** Best sustained average at that duration, in watts. */
  value: number
  /** Activity holding that best average, when recorded. */
  activityId: number | null
}

/** The hyperbolic fit recomputed for one power-duration response. */
export interface PowerDurationFit {
  /** Fitted critical power in watts. */
  criticalPowerW: number
  /** Fitted anaerobic work capacity in joules. */
  wPrimeJ: number
  /** Fit error as a percentage of the mean. */
  cvPct: number
  /** Shortest duration included in this fit. */
  fitFromS: number
  /** Longest duration included in this fit. */
  fitToS: number
}

/** One activity whose samples feed the curve, offered for W' balance. */
export interface PowerDurationSession {
  activityId: number
  name: string
  startTime: string | null
}

/** One rolling-window curve (3 or 6 months back) overlaid on the record. */
export interface PowerDurationOverlay {
  /** Window length in months, back from today. */
  months: number
  points: DurationPeak[]
  /** The window's own fit: its CP is what shows how form is moving. */
  fit: PowerDurationFit | null
}

/** Duration-intensity curve of one athlete and sport. */
export interface PowerDuration {
  sport: string
  points: DurationPeak[]
  fit: PowerDurationFit | null
  threshold: PowerThreshold | null
  sessions: PowerDurationSession[]
  overlays: PowerDurationOverlay[]
}

/** One session's own fitted critical power, oldest first in the trend. */
export interface CpTrendPoint {
  activityId: number
  /** Server date the session belongs to. */
  date: string
  /** Critical power fitted from this session's sustained efforts, in watts. */
  cpW: number
  /** Anaerobic work capacity fitted alongside, an index tied to the window. */
  wPrimeJ: number
  /** Fit error as a percentage of the mean. */
  cvPct: number
  /** Where the session's watts came from, when tagged (`stryd`, ...). */
  powerSource: string | null
}

/** One session the duration-intensity curve held out, with the numbers. */
export interface PowerFlag {
  /** Session held out of the curve. */
  activityId: number
  /** Server day of the session. */
  startedOn: string
  /** Duration of the strongest implausible average, in seconds. */
  durationS: number
  /** The refused best average, in watts. */
  valueW: number
  /** The session's own normalized power, in watts. */
  sessionNpW: number
  /** ``valueW / sessionNpW``. */
  ratio: number
  /** The tier ratio the session exceeded. */
  cap: number
}

/** One sample of the evolving threshold line, on a day the trend shows. */
export interface CpTrendEnvelopePoint {
  /** Server date this threshold is answered for. */
  date: string
  /** Temporal threshold in watts - what that day's loads were divided by. */
  cpW: number
}

/** Critical-power evolution of one athlete and sport. */
export interface CpTrend {
  sport: string
  /** Days of history covered, `null` for all of it. */
  windowDays: number | null
  /** What the fits are in: watts, or metres per second for a no-power athlete. */
  measure: 'power' | 'speed'
  /** The threshold the sport's loads currently use, the reference line. */
  threshold: PowerThreshold | null
  /** The pace-side twin, the reference line of a speed trend. */
  speedThreshold: SpeedThreshold | null
  points: CpTrendPoint[]
  /** The temporal threshold line, sampled on the points' own days. */
  envelope: CpTrendEnvelopePoint[]
}

/** One sample of a W' balance series. */
export interface WprimePoint {
  /** Elapsed seconds since the activity started. */
  tS: number
  /** W' remaining at that moment, in joules. */
  remainingJ: number
}

/** W' expenditure and balance of one activity. */
export interface WprimeBalance {
  /** Critical power measured against, `null` when the sport has none. */
  cpW: number | null
  /** Total capacity the balance starts from. */
  wPrimeJ: number | null
  /** Repletion time constant used. */
  tauS: number | null
  /** Total work done above CP, in joules. */
  workAboveCpJ: number
  /** Total time spent above CP, in seconds. */
  timeAboveCpS: number
  /** Balance series, evenly thinned for transfer. */
  points: WprimePoint[]
}

export type ActivityIntervalsDto = Schemas['ActivityIntervalsRead']
export type IntervalSegmentDto = Schemas['IntervalSegmentRead']
export type IntervalRulesDto = Schemas['IntervalRulesRead']

/** What one stretch of an activity was, as the server read it. */
export type IntervalSegmentKind = 'interval' | 'climb' | 'race' | 'recovery' | 'tempo' | 'easy'

/** How the session as a whole reads. */
export type IntervalSessionShape = 'intervals' | 'fartlek' | 'continuous' | 'terrain' | 'steady'

/** One stretch of an activity's timeline. */
export interface IntervalSegment {
  kind: IntervalSegmentKind
  /** Seconds from the start of the sampled data. */
  startS: number
  durationS: number
  /** Ground covered; 0 when the activity carries no speed stream. */
  distanceM: number
  avgPowerW: number
  /** Mean power over the athlete's threshold for the sport. */
  intensityFactor: number
  /** Mean power over the session's own median. */
  contrast: number
  avgGradePct: number | null
  avgSpeedMs: number | null
  /** Grade-equivalent pace (GAP/RAP) of the segment; null without a critical speed. */
  paceAdjustedMs: number | null
  /** Ground risen through; null when the activity carries no altitude stream. */
  elevationGainM: number | null
  /** Ground fallen through; null when the activity carries no altitude stream. */
  elevationLossM: number | null
  /** Shared-scale aRSS score of the stretch (same kernel as the daily rss_total). */
  rss: number | null
  /** That score per minute of the stretch, the mean stress rate. */
  rssPerMin: number | null
}

/** The effective numeric gates one session's split used, for the footnote. */
export interface IntervalRules {
  /** Smoothed power (W) at which an effort begins. */
  openW: number
  /** Smoothed power (W) at which it ends. */
  closeW: number
  minSegmentS: number
  shortSegmentS: number
  shortBase: number
  strongBase: number
  recoveryW: number
  effortIf: number
  climbGradePct: number
  climbIf: number
}

/** How one activity breaks down into efforts. */
export interface ActivityIntervals {
  shape: IntervalSessionShape
  /** Threshold the intensities were divided by, `null` when the sport has none. */
  thresholdW: number | null
  /** The session's own median power, the relative anchor the split used. */
  basePowerW: number
  segments: IntervalSegment[]
  /** The numeric gates used, `null` when nothing was segmented. */
  rules: IntervalRules | null
  /** The activity scored by every available source, band by band. */
  rssBySource: RssSourceTotals[]
  /** Climbs the route itself shows; independent of the segments and never summed. */
  climbs: TerrainClimb[]
}

export type TerrainClimbDto = Schemas['TerrainClimbRead']

/** One climb the smoothed altitude profile shows along the route. */
/** One signal's band totals over a whole activity (the source benchmark). */
export interface RssSourceTotals {
  source: 'power' | 'pace' | 'hr'
  total: number | null
  up: number | null
  high: number | null
  mid: number | null
  low: number | null
}

export interface TerrainClimb {
  /** Ground position where the climb starts, metres from the start of the data. */
  startM: number
  endM: number
  /** Ground climbed over. */
  lengthM: number
  /** Seconds from the start of the sampled data. */
  startS: number
  durationS: number
  elevationGainM: number
  /** Ground let go inside the climb (dips that did not break it). */
  elevationLossM: number
  /** Net grade over the whole climb, percent. */
  avgGradePct: number
  avgPowerW: number | null
  intensityFactor: number | null
  contrast: number | null
  rss: number | null
  /** Which signal scored it: power, grade-adjusted pace or heart rate. */
  rssSource: 'power' | 'pace' | 'hr' | null
  /** The score per minute of climbing, the comparable rate across climbs. */
  rssPerMin: number | null
  /** Grade-equivalent pace (GAP/RAP) of the climb; null without a critical speed. */
  rapMs: number | null
  /** The climb scored by every available source, for the benchmark. */
  sourceScores: Record<string, number> | null
}

export type BestTimesDto = Schemas['BestTimesRead']
export type BestTimeDto = Schemas['BestTimeRead']

/** One best split over a canonical race distance. */
export interface BestTime {
  /** Distance the split covered, in metres. */
  distanceM: number
  /** Time it took, in seconds. */
  timeS: number
  /** Mean power across the split; null when the activity had no power stream. */
  avgPowerW: number | null
  activityId: number
  /** Start of the activity holding the split, for the link to its day. */
  startTime: string | null
}

/** One athlete's best-splits table. */
export interface BestTimes {
  sport: string
  months: number | null
  records: BestTime[]
}

export type LapDynamicsDto = Schemas['LapDynamicsRead']

/** One lap's derived dynamics, as the analysis module measured it. */
export interface LapDynamics {
  /** The lap these dynamics belong to (join key with the activity's laps). */
  lapId: number
  avgPowerW: number | null
  /** Ground covered per cycle, in metres, when cadence or cycles allow it. */
  strideLengthM: number | null
  /** Ascent the device recorded for the lap. */
  elevationGainM: number | null
  /** Descent the device recorded for the lap, kept apart from the ascent. */
  elevationLossM: number | null
  /** Shared-scale aRSS score of the lap's window; the rows add up to the activity's. */
  rss: number | null
  /** Which signal scored it: power, grade-adjusted pace or heart rate. */
  rssSource: 'power' | 'pace' | 'hr' | null
  /** That score per minute of the lap, the mean stress rate. */
  rssPerMin: number | null
  /** Grade-equivalent pace (GAP/RAP) of the lap; null without a critical speed. */
  rapMs: number | null
  /** The same window scored by every available source, for the benchmark. */
  sourceScores: Record<string, number> | null
}

/** DTO of the batched per-activity loads endpoint. */
export type ActivityLoadsSummaryDto = Schemas['ActivityLoadsSummaryRead']

/** DTO of one stored load row. */
export type ActivityLoadDto = Schemas['ActivityLoadRead']

/** One stored load of an activity, as measured by one formula. */
export interface ActivityLoad {
  /** Formula that produced it. */
  method: LoadMethod
  /** Stress score (100 = one hour held at threshold). */
  load: number
  /** Intensity the load was derived from, when the formula has one. */
  intensityFactor: number | null
  /** Threshold value the formula divided by, when it used one. */
  thresholdUsed: number | null
}

/** Everything the list and the detail strip need to know about one activity's scores. */
export interface ActivityLoadsSummary {
  /** Whether the watts shown for this activity were recalibrated (Stryd-era). */
  powerRecalibrated: boolean
  /** Every formula's load, keyed for O(1) lookup in cards and strips. */
  loads: Partial<Record<LoadMethod, ActivityLoad>>
}

export type TrailSectionsDto = Schemas['TrailSectionsRead']
export type TrailSectionDto = Schemas['TrailSectionRead']
export type TrailOsmDto = Schemas['TrailOsmRead']
export type TrailMarkDto = Schemas['TrailMarkRead']
export type TrailNetworkDto = Schemas['TrailNetworkRead']
export type TrailNetworkWayDto = Schemas['TrailNetworkWayRead']
export type TrailVariantsDto = Schemas['TrailVariantsRead']
export type SegmentNetworkDto = Schemas['SegmentNetworkRead']
export type RouteSegmentMatchDto = Schemas['RouteSegmentMatchRead']
export type RouteDto = Schemas['RouteRead']
export type TrailPointDto = Schemas['TrailPointRead']

/** What the athlete says about a stretch the detector claimed. */
export type TrailVerdict = 'agree' | 'overstated' | 'wrong'

/** Which way the ground moved through a technical stretch. */
export type TrailSectionDirection = 'descent' | 'ascent' | 'flat'

/** What OpenStreetMap claims about the ground a stretch lies on, as it claims it. */
export interface TrailOsm {
  /** The OpenStreetMap way id. */
  wayId: number
  /** Way name, when mapped. */
  name: string | null
  /** Hiking difficulty (T0-T6), when mapped. */
  sacScale: string | null
  /** IMBA-style difficulty (0-5), when mapped. */
  mtbScale: string | null
  /** What the ground is made of, when mapped. */
  surface: string | null
  /** How rough the ride reads, when mapped. */
  smoothness: string | null
  /** How drivable the track is, when mapped. */
  tracktype: string | null
  /** Whether the path can be found, when mapped. */
  trailVisibility: string | null
  /** Way width, when mapped. */
  width: string | null
  /** How the way itself is signed to climb, when mapped. */
  incline: string | null
  /** Offset along the way where the stretch starts. */
  startM: number
  /** Offset along the way where it ends. */
  endM: number
  /** Ground of the stretch the way accounts for. */
  coveredM: number
  /** Day of the extract this was read from. */
  cacheDate: string
}

/** One technical stretch of a trail run, with its own polyline for the map. */
export interface TrailSection {
  /** Ground position where it starts, metres from the start of the data. */
  startM: number
  /** Ground covered by the stretch. */
  lengthM: number
  /** Mean grade over it, in percent. */
  gradePct: number
  /** Mean ground speed through it, m/s. */
  avgSpeedMs: number
  /** How far below the grade-explained pace it ran (0-1). */
  gapDeficit: number
  /** Cadence instability over it (0-1). */
  cadenceJitter: number
  /** Downhill braking share over it (0-1). */
  brakeScore: number
  /** Peak smoothed technicality index inside it. */
  score: number
  /** Ground position where the hard block inside starts, when there is one. */
  coreStartM: number | null
  /** Ground position where that block ends. */
  coreEndM: number | null
  /** Peak index inside the hard block. */
  coreScore: number | null
  direction: TrailSectionDirection
  /** The section's own polyline, for map overlays. */
  points: GeoPoint[]
  /** What the cartography says about this ground, when it says anything. */
  osm: TrailOsm | null
  /** The athlete's own verdict on it, when they have placed one. */
  mark: TrailVerdict | null
}

/** The technical-terrain pass over one activity (experimental). */
export interface TrailSections {
  /** The session's own flat-ground reference pace, m/s. */
  vBaseMs: number
  /** The index cut the detector used; grades read their sections against it. */
  threshold: number
  /** Multiple of the cut that reads as firmly technical. */
  technicalRatio: number
  /** Multiple of the cut that counts as a hard core. */
  hardRatio: number
  /** Longest stretch that may read as extreme as a whole. */
  veryMaxLengthM: number
  /** Whether the stride/cadence terms could be used at all. */
  cadenceAvailable: boolean
  /** Total metres flagged technical. */
  technicalM: number
  /** Total ground covered. */
  totalM: number
  /** The technical stretches, in ground order. */
  sections: TrailSection[]
}

/** What a way's own descents back, once enough of them agree. */
export type TrailClaim = 'hard' | 'technical' | 'mild' | 'thin' | 'plain'

/** One mapped way of the crossed network, judged by its descents. */
export interface TrailNetworkWay {
  /** The OSM way this is. */
  wayId: number
  /** The way's mapped name, when it carries one. */
  name: string | null
  /** What its descents back. */
  claim: TrailClaim
  /** Distinct sessions whose track ran over it, technical or not. */
  crossingsTotal: number
  /** Distinct sessions that went down it: the measurement. */
  descentCrossings: number
  /** Distinct sessions that crossed it the other way: corroboration only. */
  ascentCrossings: number
  /** Descent crossings that contained a hard core. */
  hardCrossings: number
  /** Median, across descents, of the harshest index each reached; null when it has none. */
  medScore: number | null
  /** First quartile of that distribution. */
  q1: number | null
  /** Third quartile of it. */
  q3: number | null
  /** The gentlest descent of it. */
  minScore: number | null
  /** The harshest descent of it. */
  maxScore: number | null
  /** Median grade of the descent crossings, percent. */
  medGradePct: number | null
  /** The way's length as drawn, metres. */
  lengthM: number
  /** The ordered polyline: what the map draws and a route would join. */
  points: GeoPoint[]
  /** The way's mapped hiking difficulty, when mapped. */
  sacScale: string | null
  /** The way's mapped MTB difficulty, when mapped. */
  mtbScale: string | null
  /** The way's mapped surface, when mapped. */
  surface: string | null
  /** The way's mapped track grade, when mapped. */
  tracktype: string | null
}

/** The crossed network: ways with measured descents, best claim first. */
export interface TrailNetwork {
  /** The detector stamp the observations carry. */
  method: string
  /** The index cut the detector used. */
  threshold: number
  /** Multiple of the cut a median needs to read firmly technical. */
  technicalRatio: number
  /** Multiple of the cut for a hard claim. */
  hardRatio: number
  /** Descents a way needs before anything is claimed for it. */
  minDescents: number
  /** The ways, best claim first. */
  ways: TrailNetworkWay[]
}

/** The athlete's verdict on one suggested variant. */
export type VariantVerdict = 'real' | 'falsa'

/** One reconnection variant suggested along a planned route. */
export interface TrailVariant {
  /** Ground position of the junction, metres from the route start. */
  splitM: number
  /** Ground position where the variant joins back. */
  rejoinM: number
  /** Which side it leaves the route at the junction. */
  side: 'left' | 'right' | 'straight'
  /** Ground the variant covers, metres. */
  variantM: number
  /** Ground of the stretch it replaces, metres. */
  mainM: number
  /** Variant minus main. */
  deltaM: number
  /** Variant minus main elevation gain, metres. */
  deltaGainM: number
  /** How firmly the personal graph evidences it. */
  status: 'confirmada' | 'provisional'
  /** Hardest technical claim found along it. */
  grade: string
  /** The drawn polyline, shaped by certified ways where real. */
  points: GeoPoint[]
  /** The athlete's own segment it runs along, when one carries most of it. */
  segment: string | null
  /** The athlete's verdict on it, when placed. */
  mark: VariantVerdict | null
}

/** The variant analysis of one planned route. */
export interface TrailVariants {
  /** Recorded route the variants were searched along, when it was one. */
  activityId: number | null
  /** Designed route the variants were searched along, when it was one. */
  routeId: number | null
  /** Grid size the search walked on, metres. */
  cellM: number
  /** The suggestions, junction order. */
  variants: TrailVariant[]
}

/** A route the athlete designed over their own network. */
export interface DesignRoute {
  /** Route id. */
  id: number
  /** Route name. */
  name: string
  /** Ordered polyline as saved. */
  points: GeoPoint[]
  /** Ground covered, metres. */
  distanceM: number
  /** Elevation gain, metres. */
  gainM: number
  /** Free-text remarks. */
  notes: string | null
  /** Creation instant, ISO. */
  createdAt: string
}

/** A live answer for a drawn route: the shaped line with one kind and one
end index per anchor-to-anchor stretch (the stretches share their boundary). */
export interface RoutePreview {
  /** The shaped geometry, in order. */
  points: GeoPoint[]
  /** Whose network carried each stretch. */
  carried: ('walked' | 'mapped' | 'straight')[]
  /** Position in points where each stretch ends, inclusive. */
  spans: number[]
}

/** One named segment as the athlete's own network knows it. */
export interface SegmentNetworkItem {
  /** The segment this is. */
  segmentId: number
  /** The athlete's own name for it. */
  name: string
  /** What its descents back: same bands as the mapped network. */
  claim: TrailClaim
  /** Segment length along its geometry, metres. */
  lengthM: number
  /** Positive gain along the geometry, when the source knew it. */
  gainM: number | null
  /** Recorded traversals of it. */
  crossingsTotal: number
  /** Traversals that held measured technical descents. */
  descentCrossings: number
  /** Median of the harshest descent index per traversal. */
  medScore: number | null
  /** First quartile of that distribution. */
  q1: number | null
  /** Third quartile of it. */
  q3: number | null
  /** Median mean grade of its traversals, percent. */
  medGradePct: number | null
  /** Whether it is a short closed circuit rather than travel. */
  loop: boolean
  /** The stored polyline, for the map. */
  points: GeoPoint[]
}

/** The athlete's named network: segments claimed like ways. */
export interface SegmentNetwork {
  /** Traversals required before a segment is claimed. */
  minDescents: number
  /** The detector's own threshold the bands are multiples of. */
  threshold: number
  /** Multiple of the threshold that reads firmly technical. */
  technicalRatio: number
  /** Multiple of the threshold that reads as a hard core. */
  hardRatio: number
  /** Segments with at least one crossing, best claim first. */
  segments: SegmentNetworkItem[]
}

/** One of the athlete's own segments a saved route runs along. */
export interface RouteSegmentMatch {
  /** The segment this is. */
  segmentId: number
  /** The athlete's name for it. */
  name: string
  /** What its descents back in the named network. */
  claim: TrailClaim
  /** Along-segment span the route follows. */
  alongStartM: number
  alongEndM: number
  /** Whether the route goes with or against the segment's own direction. */
  direction: 'forward' | 'reverse'
  /** Segment length the route runs along, metres. */
  coveredM: number
  /** Full segment length, metres. */
  lengthM: number
  /** Where along the route the run begins, metres. */
  routeStartM: number
  /** Where along the route the run ends, metres. */
  routeEndM: number
}

/** Intention the A→B generator searches with. */
export type RouteGenMode = 'curt' | 'tecnic' | 'tanteig'

/** One stretch of a generated plan that stays provisional. */
export interface RoutePlanUnknownRange {
  /** Ground position along the plan where unwalked mapped ground begins. */
  startM: number
  endM: number
}

/** A generated plan, offered for editing before it is ever saved. */
export interface RoutePlan {
  mode: RouteGenMode
  /** The plan's polyline, ready to become a draft. */
  points: GeoPoint[]
  distanceM: number
  gainM: number
  /** Total provisional ground, metres. */
  unknownM: number
  unknownRanges: RoutePlanUnknownRange[]
}

/** The athlete's own pace rule, as published by the guide pack. */
export interface RouteGuidePace {
  /** Seconds per kilometre of flat ground. */
  perKmS: number
  /** Extra seconds per 100 m climbed. */
  per100mGainS: number
  /** Extra seconds per 100 m descended. */
  per100mLossS: number
  /** Extra seconds per kilometre of technical ground. */
  perTechnicalKmS: number
  /** Median relative miss over the sessions it was fitted on (0-1). */
  error: number
  /** Sessions it was fitted on. */
  sessions: number
}

/** One mapped water point near the route. */
export interface RouteGuideWater {
  /** What the map vouches: ``font`` is drinking water, ``spring`` nobody promises. */
  kind: 'font' | 'spring'
  /** Mapped name, when the map carries one. */
  name: string | null
  /** Ground position along the route, metres. */
  atM: number
  /** Where it sits beside the line, for the map. */
  point: GeoPoint
}

/** The guide pack, trimmed to what the route view shows. */
export interface RouteGuide {
  distanceM: number
  gainM: number
  lossM: number
  /** Predicted moving time, or ``null`` when there is no pace rule yet. */
  estTimeS: number | null
  pace: RouteGuidePace | null
  water: RouteGuideWater[]
  /** Ground never crossed before, metres. */
  unknownM: number
}
