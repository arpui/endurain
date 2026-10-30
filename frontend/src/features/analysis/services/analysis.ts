/**
 * API service for training-load analysis: the fitness series, the athlete's
 * parameters, and the two queued background passes. All calls go through
 * `apiFetch` and map DTOs to the feature's clean models.
 */

import type {
  AnalysisSettings,
  AnalysisSettingsDto,
  AnalysisSettingsUpdateDto,
  FitnessQuery,
  FitnessSeries,
  FitnessSeriesDto,
  LoadMethod,
  ThresholdSource,
} from '../types'
import type { Schemas } from '@/types'

import { apiFetch } from '@/services/http'

/** Maps one threshold-provenance value, defaulting to derived when absent. */
function mapSource(source: Schemas['ThresholdSource'] | undefined): ThresholdSource {
  return source === 'manual' ? 'manual' : 'auto'
}

/** Maps a settings DTO to the domain model, filling empty collections. */
export function mapSettings(dto: AnalysisSettingsDto): AnalysisSettings {
  const power: AnalysisSettings['powerThresholds'] = {}
  for (const [sport, entry] of Object.entries(dto.power_thresholds ?? {})) {
    power[sport] = {
      valueW: entry.value_w,
      wPrimeJ: entry.w_prime_j ?? null,
      source: mapSource(entry.source),
      cvPct: entry.cv_pct ?? null,
    }
  }
  return {
    acuteDays: dto.acute_days,
    chronicDays: dto.chronic_days,
    primaryMethod: dto.primary_method as LoadMethod,
    restingHr: dto.resting_hr ?? null,
    powerThresholds: power,
    hrThreshold: dto.hr_threshold
      ? { valueBpm: dto.hr_threshold.value_bpm, source: mapSource(dto.hr_threshold.source) }
      : null,
    methodParams: { ...dto.method_params },
  }
}

/** Maps a fitness-series DTO, keeping every point's numeric fields. */
export function mapFitnessSeries(dto: FitnessSeriesDto): FitnessSeries {
  return {
    method: dto.method as LoadMethod,
    acuteDays: dto.acute_days,
    chronicDays: dto.chronic_days,
    sport: dto.sport ?? null,
    points: (dto.points ?? []).map((point) => ({
      date: point.date,
      load: point.load,
      acute: point.acute,
      chronic: point.chronic,
      balance: point.balance,
    })),
  }
}

/**
 * Fetches the daily load series with the fatigue/fitness/form curves.
 *
 * @param query - Filters to apply; unnamed ones keep the stored defaults.
 * @param signal - Optional abort signal for TanStack Query cancellation.
 * @returns The series with the parameters that produced it.
 * @throws {HttpError} When the request fails.
 */
export async function fetchFitnessSeries(
  query: FitnessQuery,
  signal?: AbortSignal,
): Promise<FitnessSeries> {
  const params = new URLSearchParams()
  if (query.method) params.set('method', query.method)
  if (query.acuteDays !== undefined) params.set('acute_days', String(query.acuteDays))
  if (query.chronicDays !== undefined) params.set('chronic_days', String(query.chronicDays))
  if (query.sport) params.set('sport', query.sport)
  if (query.fromDate) params.set('from', query.fromDate)
  if (query.toDate) params.set('to', query.toDate)
  const suffix = params.size > 0 ? `?${params.toString()}` : ''
  const dto = await apiFetch<FitnessSeriesDto>(`/analysis/fitness${suffix}`, { signal })
  return mapFitnessSeries(dto)
}

/**
 * Fetches the caller's analysis parameters.
 *
 * @param signal - Optional abort signal for TanStack Query cancellation.
 * @returns The stored settings (the server answers defaults when none are stored).
 * @throws {HttpError} When the request fails.
 */
export async function fetchAnalysisSettings(signal?: AbortSignal): Promise<AnalysisSettings> {
  const dto = await apiFetch<AnalysisSettingsDto>('/analysis/settings', { signal })
  return mapSettings(dto)
}

/**
 * Updates the caller's analysis parameters and returns the stored result.
 *
 * @param payload - Only the changed fields; nulls clear thresholds.
 * @returns The settings after the update.
 * @throws {HttpError} When the request fails.
 */
export async function updateAnalysisSettings(
  payload: AnalysisSettingsUpdateDto,
): Promise<AnalysisSettings> {
  const dto = await apiFetch<AnalysisSettingsDto>('/analysis/settings', {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
  return mapSettings(dto)
}

/**
 * Queues re-deriving the caller's thresholds from their own records.
 *
 * @returns Nothing when the refresh was accepted.
 * @throws {HttpError} When the request fails.
 */
export async function queueThresholdRefresh(): Promise<void> {
  await apiFetch<{ queued: boolean }>('/analysis/thresholds/refresh', {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

/**
 * Queues re-measuring every activity with the current thresholds and formulas.
 *
 * @returns Nothing when the recompute was accepted.
 * @throws {HttpError} When the request fails.
 */
export async function queueLoadRecompute(): Promise<void> {
  await apiFetch<{ queued: boolean }>('/analysis/recompute', {
    method: 'POST',
    body: JSON.stringify({}),
  })
}
