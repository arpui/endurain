/**
 * Domain models for training-load analysis. DTO aliases derive from the
 * generated client so a backend contract change surfaces as a compile error in
 * the service mappers.
 */

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

/** Load formula keys, mirroring the backend `LoadMethod` enum. */
export type LoadMethod =
  | 'coggan_tss'
  | 'skiba_score'
  | 'hr_load'
  | 'trimp'
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
export type IntervalSegmentKind = 'interval' | 'climb' | 'race' | 'recovery' | 'easy'

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
  /** Climbs the route itself shows; independent of the segments and never summed. */
  climbs: TerrainClimb[]
}

export type TerrainClimbDto = Schemas['TerrainClimbRead']

/** One climb the smoothed altitude profile shows along the route. */
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
}

export type BestTimesDto = Schemas['BestTimesRead']
export type BestTimeDto = Schemas['BestTimeRead']

/** One best split over a canonical race distance. */
export interface BestTime {
  /** Distance the split covered, in metres. */
  distanceM: number
  /** Time it took, in seconds. */
  timeS: number
  /** Mean power across the split, in watts. */
  avgPowerW: number
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
}
