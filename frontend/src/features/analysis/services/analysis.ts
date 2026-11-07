/**
 * API service for training-load analysis: the fitness series, the athlete's
 * parameters, and the two queued background passes. All calls go through
 * `apiFetch` and map DTOs to the feature's clean models.
 */

import type {
  ActivityIntervals,
  ActivityIntervalsDto,
  LapDynamics,
  LapDynamicsDto,
  BestTime,
  BestTimeDto,
  BestTimes,
  BestTimesDto,
  AnalysisSettings,
  AnalysisSettingsDto,
  AnalysisSettingsUpdateDto,
  FitnessQuery,
  FitnessSeries,
  FitnessSeriesDto,
  IntervalRules,
  IntervalRulesDto,
  IntervalSegment,
  IntervalSegmentDto,
  TerrainClimb,
  TerrainClimbDto,
  LoadMethod,
  DurationPeak,
  DurationPeakDto,
  PowerFlag,
  PowerFlagDto,
  PowerDuration,
  PowerDurationDto,
  PowerDurationFit,
  PowerDurationFitDto,
  CpTrend,
  CpTrendDto,
  ThresholdSource,
  WprimeBalance,
  WprimeBalanceDto,
  ActivityLoad,
  ActivityLoadDto,
  ActivityLoadsSummary,
  ActivityLoadsSummaryDto,
  RouteDto,
  SegmentNetworkDto,
  TrailMarkDto,
  TrailNetworkDto,
  TrailVariantsDto,
  TrailNetworkWay,
  TrailSection,
  DesignRoute,
  SegmentNetwork,
  SegmentNetworkItem,
  TrailNetwork,
  TrailVariant,
  TrailVariants,
  TrailSections,
  TrailSectionsDto,
  TrailVerdict,
} from '../types'
import type { GeoPoint } from '@/composables/useMapProvider'
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
  const speed: AnalysisSettings['speedThresholds'] = {}
  for (const [sport, entry] of Object.entries(dto.speed_thresholds ?? {})) {
    speed[sport] = {
      valueMs: entry.value_ms,
      wPrimeM: entry.w_prime_m ?? null,
      source: mapSource(entry.source),
      cvPct: entry.cv_pct ?? null,
    }
  }
  const paceCalibrations: AnalysisSettings['paceCalibrations'] = {}
  for (const [sport, entry] of Object.entries(dto.pace_calibrations ?? {})) {
    paceCalibrations[sport] = {
      slopeWPerMs: entry.slope_w_per_ms,
      interceptW: entry.intercept_w,
      r2: entry.r2 ?? null,
      gradeWPerMsPct: entry.grade_w_per_ms_pct ?? 0,
      source: mapSource(entry.source),
    }
  }
  return {
    acuteDays: dto.acute_days,
    chronicDays: dto.chronic_days,
    primaryMethod: dto.primary_method as LoadMethod,
    fitFromS: dto.fit_from_s ?? null,
    fitToS: dto.fit_to_s ?? null,
    restingHr: dto.resting_hr ?? null,
    powerThresholds: power,
    speedThresholds: speed,
    paceCalibrations,
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

/** Maps a power-duration DTO (curve, fit, threshold, sessions, overlays) to the domain model. */
export function mapPowerDuration(dto: PowerDurationDto): PowerDuration {
  const entry = dto.threshold
  return {
    sport: dto.sport,
    points: (dto.points ?? []).map(mapDurationPeak),
    fit: mapPowerDurationFit(dto.fit),
    threshold: entry
      ? {
          valueW: entry.value_w,
          wPrimeJ: entry.w_prime_j ?? null,
          source: mapSource(entry.source),
          cvPct: entry.cv_pct ?? null,
        }
      : null,
    sessions: (dto.sessions ?? []).map((session) => ({
      activityId: session.activity_id,
      name: session.name,
      startTime: session.start_time ?? null,
    })),
    overlays: (dto.overlays ?? []).map((overlay) => ({
      months: overlay.months,
      points: (overlay.points ?? []).map(mapDurationPeak),
      fit: mapPowerDurationFit(overlay.fit),
    })),
  }
}

/** Maps one duration-intensity point of a curve or a window overlay. */
function mapDurationPeak(point: DurationPeakDto): DurationPeak {
  return {
    durationS: point.duration_s,
    value: point.value,
    activityId: point.activity_id ?? null,
  }
}

/** Maps a hyperbolic fit payload, shared by the curve and its window overlays. */
function mapPowerDurationFit(dto: PowerDurationFitDto | null | undefined): PowerDurationFit | null {
  return dto
    ? {
        criticalPowerW: dto.critical_power_w,
        wPrimeJ: dto.w_prime_j,
        cvPct: dto.cv_pct,
        fitFromS: dto.fit_from_s,
        fitToS: dto.fit_to_s,
      }
    : null
}

/** Maps a W' balance DTO to the domain model. */
export function mapWprimeBalance(dto: WprimeBalanceDto): WprimeBalance {
  return {
    cpW: dto.cp_w ?? null,
    wPrimeJ: dto.w_prime_j ?? null,
    tauS: dto.tau_s ?? null,
    workAboveCpJ: dto.work_above_cp_j,
    timeAboveCpS: dto.time_above_cp_s,
    points: (dto.points ?? []).map((point) => ({ tS: point.t_s, remainingJ: point.remaining_j })),
  }
}

/**
 * Fetches the duration-intensity curve of one sport for the caller.
 *
 * @param sport - Sport group to read.
 * @param window - Optional preview fit window; stored settings decide when omitted.
 * @param signal - Optional abort signal for TanStack Query cancellation.
 * @returns The curve with its recomputed fit and the effective threshold.
 * @throws {HttpError} When the request fails.
 */
export async function fetchPowerDuration(
  sport: string,
  window: { fitFromS: number | null; fitToS: number | null },
  signal?: AbortSignal,
): Promise<PowerDuration> {
  const params = new URLSearchParams({ sport })
  if (window.fitFromS !== null) params.set('fit_from', String(window.fitFromS))
  if (window.fitToS !== null) params.set('fit_to', String(window.fitToS))
  const dto = await apiFetch<PowerDurationDto>(`/analysis/power-duration?${params.toString()}`, {
    signal,
  })
  return mapPowerDuration(dto)
}

/**
 * Fetches one activity's W' balance and above-CP totals.
 *
 * @param activityId - The activity to measure.
 * @param signal - Optional abort signal for TanStack Query cancellation.
 * @returns The balance series and its summary.
 * @throws {HttpError} When the request fails.
 */
export async function fetchWprimeBalance(
  activityId: number,
  signal?: AbortSignal,
): Promise<WprimeBalance> {
  const dto = await apiFetch<WprimeBalanceDto>(`/activities/${activityId}/wprime`, { signal })
  return mapWprimeBalance(dto)
}

/**
 * Fetches the caller's critical-power evolution for one sport.
 *
 * @param sport - Sport group to read.
 * @param windowDays - Days of history, `null` for all of it.
 * @param signal - Optional abort signal for TanStack Query cancellation.
 * @returns The per-session CP fits and the current reference threshold.
 * @throws {HttpError} When the request fails.
 */
export async function fetchCpTrend(
  sport: string,
  windowDays: number | null,
  signal?: AbortSignal,
): Promise<CpTrend> {
  const params = new URLSearchParams({ sport })
  if (windowDays !== null) params.set('window', String(windowDays))
  const dto = await apiFetch<CpTrendDto>(`/analysis/cp-trend?${params.toString()}`, { signal })
  return mapCpTrend(dto)
}

/**
 * Fetches the sessions the caller's duration-intensity curve held out.
 *
 * @param sport - Sport group to read.
 * @param signal - Optional abort signal for TanStack Query cancellation.
 * @returns The refused sessions and the numbers behind each refusal.
 * @throws {HttpError} When the request fails.
 */
export async function fetchPowerFlags(sport: string, signal?: AbortSignal): Promise<PowerFlag[]> {
  const dto = await apiFetch<PowerFlagDto[]>(
    `/analysis/power-flags?sport=${encodeURIComponent(sport)}`,
    { signal },
  )
  return dto.map((flag) => ({
    activityId: flag.activity_id,
    startedOn: flag.started_on,
    durationS: flag.duration_s,
    valueW: flag.value_w,
    sessionNpW: flag.session_np_w,
    ratio: flag.ratio,
    cap: flag.cap,
  }))
}

/** Maps a critical-power trend DTO (points, reference threshold) to the domain model. */
function mapCpTrend(dto: CpTrendDto): CpTrend {
  const entry = dto.threshold
  const speedEntry = dto.speed_threshold
  return {
    sport: dto.sport,
    windowDays: dto.window_days ?? null,
    measure: dto.measure === 'speed' ? 'speed' : 'power',
    threshold: entry
      ? {
          valueW: entry.value_w,
          wPrimeJ: entry.w_prime_j ?? null,
          source: mapSource(entry.source),
          cvPct: entry.cv_pct ?? null,
        }
      : null,
    speedThreshold: speedEntry
      ? {
          valueMs: speedEntry.value_ms,
          wPrimeM: speedEntry.w_prime_m ?? null,
          source: mapSource(speedEntry.source),
          cvPct: speedEntry.cv_pct ?? null,
        }
      : null,
    points: (dto.points ?? []).map((point) => ({
      activityId: point.activity_id,
      date: point.date,
      cpW: point.cp_w,
      wPrimeJ: point.w_prime_j,
      cvPct: point.cv_pct,
      powerSource: point.power_source ?? null,
    })),
    envelope: (dto.envelope ?? []).map((sample) => ({ date: sample.date, cpW: sample.cp_w })),
  }
}

/** Maps one segment of the interval analysis to the domain model. */
function mapIntervalSegment(dto: IntervalSegmentDto): IntervalSegment {
  return {
    kind: dto.kind as IntervalSegment['kind'],
    startS: dto.start_s,
    durationS: dto.duration_s,
    distanceM: dto.distance_m,
    avgPowerW: dto.avg_power_w,
    intensityFactor: dto.intensity_factor,
    contrast: dto.contrast,
    avgGradePct: dto.avg_grade_pct ?? null,
    avgSpeedMs: dto.avg_speed_ms ?? null,
    elevationGainM: dto.elevation_gain_m ?? null,
    elevationLossM: dto.elevation_loss_m ?? null,
    rss: dto.rss ?? null,
    rssPerMin: dto.rss_per_min ?? null,
    paceAdjustedMs: dto.pace_adjusted_ms ?? null,
  }
}

/** Maps one terrain-climb DTO. */
function mapTerrainClimb(dto: TerrainClimbDto): TerrainClimb {
  return {
    startM: dto.start_m,
    endM: dto.end_m,
    lengthM: dto.length_m,
    startS: dto.start_s,
    durationS: dto.duration_s,
    elevationGainM: dto.elevation_gain_m,
    elevationLossM: dto.elevation_loss_m,
    avgGradePct: dto.avg_grade_pct,
    avgPowerW: dto.avg_power_w ?? null,
    intensityFactor: dto.intensity_factor ?? null,
    contrast: dto.contrast ?? null,
    rss: dto.rss ?? null,
    rssSource: (dto.rss_source as 'power' | 'pace' | 'hr' | null) ?? null,
    sourceScores: dto.source_scores ?? null,
    rssPerMin: dto.rss_per_min ?? null,
    rapMs: dto.rap_ms ?? null,
  }
}

/** Maps an interval-analysis DTO, filling an empty timeline and no climbs. */
export function mapActivityIntervals(dto: ActivityIntervalsDto): ActivityIntervals {
  return {
    shape: dto.shape as ActivityIntervals['shape'],
    thresholdW: dto.threshold_w ?? null,
    basePowerW: dto.base_power_w,
    segments: (dto.segments ?? []).map(mapIntervalSegment),
    rules: dto.rules ? mapIntervalRules(dto.rules) : null,
    rssBySource: (dto.rss_by_source ?? []).map((row) => ({
      source: row.source as 'power' | 'pace' | 'hr',
      total: row.total ?? null,
      up: row.up ?? null,
      high: row.high ?? null,
      mid: row.mid ?? null,
      low: row.low ?? null,
    })),
    climbs: (dto.climbs ?? []).map(mapTerrainClimb),
  }
}

/**
 * Fetches which stretches of one trail run the server reads as technical.
 *
 * Experimental: the technicality index weights and threshold are still being
 * calibrated against the athlete's own judgement of known routes.
 *
 * @param activityId - The activity to analyse.
 * @param signal - Optional abort signal for TanStack Query cancellation.
 * @returns The technical sections with their polylines, plus session context.
 * @throws {HttpError} When the request fails.
 */
export async function fetchTrailSections(
  activityId: number,
  signal?: AbortSignal,
): Promise<TrailSections> {
  const dto = await apiFetch<TrailSectionsDto>(`/activities/${activityId}/trail-sections`, {
    signal,
  })
  return {
    vBaseMs: dto.v_base_ms,
    threshold: dto.threshold,
    technicalRatio: dto.technical_ratio,
    hardRatio: dto.hard_ratio,
    veryMaxLengthM: dto.very_max_length_m,
    cadenceAvailable: dto.cadence_available,
    technicalM: dto.technical_m,
    totalM: dto.total_m,
    sections: (dto.sections ?? []).map((section): TrailSection => ({
      startM: section.start_m,
      lengthM: section.length_m,
      gradePct: section.grade_pct,
      avgSpeedMs: section.avg_speed_ms,
      gapDeficit: section.gap_deficit,
      cadenceJitter: section.cadence_jitter,
      brakeScore: section.brake_score,
      score: section.score,
      coreStartM: section.core_start_m ?? null,
      coreEndM: section.core_end_m ?? null,
      coreScore: section.core_score ?? null,
      direction: section.direction as TrailSection['direction'],
      points: (section.points ?? []).map((point) => ({ lat: point.lat, lng: point.lon })),
      mark: (section.mark ?? null) as TrailVerdict | null,
      osm: section.osm
        ? {
            wayId: section.osm.way_id,
            name: section.osm.name ?? null,
            sacScale: section.osm.sac_scale ?? null,
            mtbScale: section.osm.mtb_scale ?? null,
            surface: section.osm.surface ?? null,
            smoothness: section.osm.smoothness ?? null,
            tracktype: section.osm.tracktype ?? null,
            trailVisibility: section.osm.trail_visibility ?? null,
            width: section.osm.width ?? null,
            incline: section.osm.incline ?? null,
            startM: section.osm.start_m,
            endM: section.osm.end_m,
            coveredM: section.osm.covered_m,
            cacheDate: section.osm.cache_date,
          }
        : null,
    })),
  }
}

/**
 * Fetches how one activity breaks down into efforts (reps, climbs, the steady parts).
 *
 * @param activityId - The activity to split.
 * @param signal - Optional abort signal for TanStack Query cancellation.
 * @returns The session shape and its contiguous segments.
 * @throws {HttpError} When the request fails.
 */
export async function fetchActivityIntervals(
  activityId: number,
  signal?: AbortSignal,
): Promise<ActivityIntervals> {
  const dto = await apiFetch<ActivityIntervalsDto>(`/activities/${activityId}/intervals`, {
    signal,
  })
  return mapActivityIntervals(dto)
}

/** Maps the numeric gates the server used for one session. */
function mapIntervalRules(dto: IntervalRulesDto): IntervalRules {
  return {
    openW: dto.open_w,
    closeW: dto.close_w,
    minSegmentS: dto.min_segment_s,
    shortSegmentS: dto.short_segment_s,
    shortBase: dto.short_base,
    strongBase: dto.strong_base,
    recoveryW: dto.recovery_w,
    effortIf: dto.effort_if,
    climbGradePct: dto.climb_grade_pct,
    climbIf: dto.climb_if,
  }
}

/** Maps one best-split record to the domain model. */
function mapBestTime(dto: BestTimeDto): BestTime {
  return {
    distanceM: dto.distance_m,
    timeS: dto.time_s,
    avgPowerW: dto.avg_power_w ?? null,
    activityId: dto.activity_id,
    startTime: dto.start_time ?? null,
  }
}

/** Maps a best-splits DTO to the domain model. */
export function mapBestTimes(dto: BestTimesDto): BestTimes {
  return {
    sport: dto.sport,
    months: dto.months ?? null,
    records: (dto.records ?? []).map(mapBestTime),
  }
}

/**
 * Fetches the athlete's best splits over the canonical race distances.
 *
 * @param months - Window in months back from today; ``null`` for all history.
 * @param signal - Optional abort signal for TanStack Query cancellation.
 * @returns The best-splits table, shortest distance first.
 * @throws {HttpError} When the request fails.
 */
export async function fetchBestTimes(
  months: number | null,
  signal?: AbortSignal,
): Promise<BestTimes> {
  const params = new URLSearchParams({ sport: 'run' })
  if (months !== null) params.set('months', String(months))
  const dto = await apiFetch<BestTimesDto>(`/analysis/best-times?${params.toString()}`, { signal })
  return mapBestTimes(dto)
}

/** Maps one lap-dynamics DTO to the domain model. */
function mapLapDynamics(dto: LapDynamicsDto): LapDynamics {
  return {
    lapId: dto.lap_id,
    avgPowerW: dto.avg_power_w ?? null,
    strideLengthM: dto.stride_length_m ?? null,
    elevationGainM: dto.elevation_gain_m ?? null,
    elevationLossM: dto.elevation_loss_m ?? null,
    rss: dto.rss ?? null,
    rssSource: (dto.rss_source as 'power' | 'pace' | 'hr' | null) ?? null,
    sourceScores: dto.source_scores ?? null,
    rssPerMin: dto.rss_per_min ?? null,
    rapMs: dto.rap_ms ?? null,
  }
}

/**
 * Fetches the caller's per-lap dynamics (power, stride length, elevation split,
 * aRSS) for one
 * activity, computed on read against their own threshold.
 *
 * @param activityId - The activity to analyze.
 * @param signal - Optional abort signal for TanStack Query cancellation.
 * @returns One entry per lap, in recorded order.
 * @throws {HttpError} When the request fails.
 */
export async function fetchActivityLapDynamics(
  activityId: number,
  signal?: AbortSignal,
): Promise<LapDynamics[]> {
  const dtos = await apiFetch<LapDynamicsDto[]>(`/activities/${activityId}/lap-dynamics`, {
    signal,
  })
  return (dtos ?? []).map(mapLapDynamics)
}

/** Maps one stored load row. */
function mapActivityLoad(dto: ActivityLoadDto): ActivityLoad {
  return {
    method: dto.method as ActivityLoad['method'],
    load: dto.load,
    intensityFactor: dto.intensity_factor ?? null,
    thresholdUsed: dto.threshold_used ?? null,
  }
}

/** Maps one activity's loads into a lookup keyed by formula. */
function mapActivityLoadsSummary(dto: ActivityLoadsSummaryDto): ActivityLoadsSummary {
  const loads: ActivityLoadsSummary['loads'] = {}
  for (const row of dto.loads ?? []) {
    const load = mapActivityLoad(row)
    loads[load.method] = load
  }
  return { powerRecalibrated: dto.power_recalibrated === true, loads }
}

/**
 * Fetches the stored loads and power provenance of many activities in one call.
 *
 * The activities list asks for the ids of the page it shows; the activity detail
 * asks for its own single id (it also wants the recalibration flag, which the
 * plain `/activities/{id}/loads` endpoint does not carry).
 *
 * @param activityIds - Activities to summarize (max 50 per call).
 * @param signal - Optional abort signal for TanStack Query cancellation.
 * @returns Activity id to its summary; unscored, untagged ids are absent.
 * @throws {HttpError} When the request fails.
 */
export async function fetchActivityLoadsSummaries(
  activityIds: number[],
  signal?: AbortSignal,
): Promise<Map<number, ActivityLoadsSummary>> {
  if (activityIds.length === 0) {
    return new Map()
  }
  const params = activityIds.map((id) => `activity_ids=${id}`).join('&')
  const dtos = await apiFetch<ActivityLoadsSummaryDto[]>(`/analysis/loads?${params}`, { signal })
  return new Map(dtos.map((dto) => [dto.activity_id, mapActivityLoadsSummary(dto)]))
}

/**
 * Place the athlete's verdict on one measured stretch: agreed, overstated, or
 * never technical.
 *
 * @param activityId - The session the stretch belongs to.
 * @param startM - Ground position of the stretch, as the server reported it.
 * @param verdict - The judgement.
 * @param signal - Optional abort signal.
 * @returns The stored mark.
 * @throws {HttpError} When the request fails.
 */
export async function postTrailSectionMark(
  activityId: number,
  startM: number,
  verdict: TrailVerdict,
  signal?: AbortSignal,
): Promise<void> {
  await apiFetch<TrailMarkDto>(`/activities/${activityId}/trail-sections/marks`, {
    method: 'POST',
    signal,
    body: JSON.stringify({ start_m: startM, verdict }),
  })
}

/**
 * Take back the verdict on one stretch.
 *
 * @param activityId - The session judged.
 * @param startM - Ground position of the stretch.
 * @param signal - Optional abort signal.
 * @returns Nothing; the mark is gone.
 * @throws {HttpError} When the request fails.
 */
export async function deleteTrailSectionMark(
  activityId: number,
  startM: number,
  signal?: AbortSignal,
): Promise<void> {
  const query = new URLSearchParams({ start_m: String(startM) })
  await apiFetch<void>(`/activities/${activityId}/trail-sections/marks?${query.toString()}`, {
    method: 'DELETE',
    signal,
  })
}

/**
 * Read the network of mapped ways the athlete has crossed, each judged by its
 * own descents.
 *
 * @param limit - Maximum ways to serve; the whole crossed network wants a high one.
 * @param signal - Optional abort signal.
 * @returns The crossed ways, best claim first.
 * @throws {HttpError} When the request fails.
 */
export async function fetchTrailNetwork(limit = 5000, signal?: AbortSignal): Promise<TrailNetwork> {
  const query = new URLSearchParams({ limit: String(limit) })
  const dto = await apiFetch<TrailNetworkDto>(`/analysis/trail-network?${query.toString()}`, {
    signal,
  })
  return {
    method: dto.method,
    threshold: dto.threshold,
    technicalRatio: dto.technical_ratio,
    hardRatio: dto.hard_ratio,
    minDescents: dto.min_descents,
    ways: (dto.ways ?? []).map((way): TrailNetworkWay => ({
      wayId: way.way_id,
      name: way.name ?? null,
      claim: way.claim as TrailNetworkWay['claim'],
      crossingsTotal: way.crossings_total,
      descentCrossings: way.descent_crossings,
      ascentCrossings: way.ascent_crossings,
      hardCrossings: way.hard_crossings,
      medScore: way.med_score,
      q1: way.q1,
      q3: way.q3,
      minScore: way.min_score,
      maxScore: way.max_score,
      medGradePct: way.med_grade_pct,
      lengthM: way.length_m,
      points: (way.geometry ?? []).flatMap(([lat, lng]) =>
        lat === undefined || lng === undefined ? [] : [{ lat, lng }],
      ),
      sacScale: way.sac_scale ?? null,
      mtbScale: way.mtb_scale ?? null,
      surface: way.surface ?? null,
      tracktype: way.tracktype ?? null,
    })),
  }
}

/**
 * Read the reconnection variants suggested along one recorded route.
 *
 * @param activityId - The activity whose track is the plan.
 * @param signal - Optional abort signal.
 * @returns The variant analysis (experimental).
 * @throws {HttpError} When the request fails.
 */
export async function fetchTrailVariants(
  activityId: number,
  signal?: AbortSignal,
): Promise<TrailVariants> {
  const dto = await apiFetch<TrailVariantsDto>(`/activities/${activityId}/trail-variants`, {
    signal,
  })
  return {
    activityId: dto.activity_id,
    cellM: dto.cell_m,
    variants: (dto.variants ?? []).map((v): TrailVariant => ({
      splitM: v.split_m,
      rejoinM: v.rejoin_m,
      side: v.side as TrailVariant['side'],
      variantM: v.variant_m,
      mainM: v.main_m,
      deltaM: v.delta_m,
      deltaGainM: v.delta_gain_m,
      status: v.status as TrailVariant['status'],
      grade: v.grade,
      points: (v.path_latlng ?? []).flatMap(([lat, lng]) =>
        lat === undefined || lng === undefined ? [] : [{ lat, lng }],
      ),
      mark: (v.mark ?? null) as TrailVariant['mark'],
    })),
  }
}

/**
 * Judge one suggested variant: real ground, or a suggestion to bury.
 *
 * @param activityId - The planned route judged.
 * @param splitM - Junction position, as the server reported it.
 * @param verdict - The verdict, or ``null`` to take it back.
 * @param signal - Optional abort signal.
 * @returns Nothing.
 * @throws {HttpError} When the request fails.
 */
export async function postVariantMark(
  activityId: number,
  splitM: number,
  verdict: 'real' | 'falsa' | null,
  signal?: AbortSignal,
): Promise<void> {
  if (verdict === null) {
    const query = new URLSearchParams({ split_m: String(splitM) })
    await apiFetch<void>(`/activities/${activityId}/trail-variants/marks?${query.toString()}`, {
      method: 'DELETE',
      signal,
    })
    return
  }
  await apiFetch<void>(`/activities/${activityId}/trail-variants/marks`, {
    method: 'POST',
    signal,
    body: JSON.stringify({ split_m: splitM, verdict }),
  })
}

/**
 * List the athlete's saved designed routes.
 *
 * @param signal - Optional abort signal.
 * @returns The routes, newest first.
 * @throws {HttpError} When the request fails.
 */
export async function fetchRoutes(signal?: AbortSignal): Promise<DesignRoute[]> {
  const dtos = await apiFetch<RouteDto[]>('/analysis/routes', { signal })
  return (dtos ?? []).map((r): DesignRoute => ({
    id: r.id,
    name: r.name,
    points: (r.geometry ?? []).flatMap(([lat, lng]) =>
      lat === undefined || lng === undefined ? [] : [{ lat, lng }],
    ),
    distanceM: r.distance_m,
    gainM: r.gain_m,
    notes: r.notes ?? null,
    createdAt: r.created_at,
  }))
}

/**
 * Save a designed route. The server measures the totals.
 *
 * @param name - Route name.
 * @param points - The drawn polyline.
 * @param notes - Optional remarks.
 * @param signal - Optional abort signal.
 * @returns The stored route.
 * @throws {HttpError} When the request fails.
 */
export async function createRoute(
  name: string,
  points: GeoPoint[],
  notes?: string,
  signal?: AbortSignal,
): Promise<DesignRoute> {
  const dto = await apiFetch<RouteDto>('/analysis/routes', {
    method: 'POST',
    signal,
    body: JSON.stringify({
      name,
      geometry: points.map((p) => [p.lat, p.lng]),
      notes: notes ?? null,
    }),
  })
  return {
    id: dto.id,
    name: dto.name,
    points: (dto.geometry ?? []).flatMap(([lat, lng]) =>
      lat === undefined || lng === undefined ? [] : [{ lat, lng }],
    ),
    distanceM: dto.distance_m,
    gainM: dto.gain_m,
    notes: dto.notes ?? null,
    createdAt: dto.created_at,
  }
}

/**
 * Edit a saved route: rename, redraw, re-note. The server measures the new totals.
 *
 * @param routeId - The route to edit.
 * @param name - Route name.
 * @param points - The edited polyline.
 * @param notes - Optional remarks.
 * @param signal - Optional abort signal.
 * @returns The stored route.
 * @throws {HttpError} When the request fails.
 */
export async function updateRoute(
  routeId: number,
  name: string,
  points: GeoPoint[],
  notes?: string,
  signal?: AbortSignal,
): Promise<DesignRoute> {
  const dto = await apiFetch<RouteDto>(`/analysis/routes/${routeId}`, {
    method: 'PUT',
    signal,
    body: JSON.stringify({
      name,
      geometry: points.map((pt) => [pt.lat, pt.lng]),
      notes: notes ?? null,
    }),
  })
  return {
    id: dto.id,
    name: dto.name,
    points: (dto.geometry ?? []).flatMap(([lat, lng]) =>
      lat === undefined || lng === undefined ? [] : [{ lat, lng }],
    ),
    distanceM: dto.distance_m,
    gainM: dto.gain_m,
    notes: dto.notes ?? null,
    createdAt: dto.created_at,
  }
}

/**
 * Shape clicked anchors along the athlete's own walked ground.
 *
 * Consecutive anchors that land on crossed cells are joined by the shortest
 * walked chain of the personal graph; stretches the network cannot carry keep
 * the straight line and are reported unwalked.
 *
 * @param points - The clicked polyline, in order.
 * @param signal - Optional abort signal.
 * @returns The walked geometry and one flag per stretch.
 * @throws {HttpError} When the request fails.
 */
export async function connectRoutePoints(
  points: GeoPoint[],
  signal?: AbortSignal,
): Promise<{ points: GeoPoint[]; walked: boolean[] }> {
  const dto = await apiFetch<Schemas['RouteConnectRead']>('/analysis/routes/connect', {
    method: 'POST',
    signal,
    body: JSON.stringify({ points: points.map((pt) => [pt.lat, pt.lng]) }),
  })
  return {
    points: (dto.geometry ?? []).flatMap(([lat, lng]) =>
      lat === undefined || lng === undefined ? [] : [{ lat, lng }],
    ),
    walked: (dto.walked ?? []) as boolean[],
  }
}

/**
 * Delete one saved route.
 *
 * @param routeId - The route to delete.
 * @param signal - Optional abort signal.
 * @returns Nothing.
 * @throws {HttpError} When the request fails.
 */
export async function deleteRoute(routeId: number, signal?: AbortSignal): Promise<void> {
  await apiFetch<void>(`/analysis/routes/${routeId}`, { method: 'DELETE', signal })
}

/**
 * Read the athlete's named network: segments claimed on descents alone.
 *
 * @param showLoops - Include short closed circuits (laps of a field).
 * @param signal - Optional abort signal.
 * @returns The named network (experimental).
 * @throws {HttpError} When the request fails.
 */
export async function fetchSegmentNetwork(
  showLoops = false,
  signal?: AbortSignal,
): Promise<SegmentNetwork> {
  const query = new URLSearchParams({ show_loops: String(showLoops), limit: '5000' })
  const dto = await apiFetch<SegmentNetworkDto>(`/analysis/segments-network?${query.toString()}`, {
    signal,
  })
  return {
    minDescents: dto.min_descents,
    threshold: dto.threshold,
    technicalRatio: dto.technical_ratio,
    hardRatio: dto.hard_ratio,
    segments: (dto.segments ?? []).map((row): SegmentNetworkItem => ({
      segmentId: row.segment_id,
      name: row.name,
      claim: row.claim as SegmentNetworkItem['claim'],
      lengthM: row.length_m,
      gainM: row.gain_m,
      crossingsTotal: row.crossings_total,
      descentCrossings: row.descent_crossings,
      medScore: row.med_score,
      q1: row.q1,
      q3: row.q3,
      medGradePct: row.med_grade_pct,
      loop: row.loop,
      points: (row.geometry ?? []).flatMap(([lat, lng]) =>
        lat === undefined || lng === undefined ? [] : [{ lat, lng }],
      ),
    })),
  }
}
