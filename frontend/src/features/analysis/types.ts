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

/** Load formula keys, mirroring the backend `LoadMethod` enum. */
export type LoadMethod = 'coggan_tss' | 'skiba_score' | 'hr_load' | 'trimp'

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
  /** Resting heart rate stated by the athlete. */
  restingHr: number | null
  /** Power threshold per sport group. */
  powerThresholds: Record<string, PowerThreshold>
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
