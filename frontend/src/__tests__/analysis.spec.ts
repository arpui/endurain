import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Schemas } from '@/types'
import type { FitnessQuery } from '@/features/analysis/types'

import { apiFetch } from '@/services/http'
import {
  fetchActivityIntervals,
  fetchAnalysisSettings,
  fetchBestTimes,
  fetchFitnessSeries,
  fetchPowerDuration,
  fetchWprimeBalance,
  mapActivityIntervals,
  mapFitnessSeries,
  mapSettings,
  queueLoadRecompute,
  queueThresholdRefresh,
  updateAnalysisSettings,
} from '@/features/analysis/services/analysis'

vi.mock('@/services/http', () => ({
  apiFetch: vi.fn<typeof apiFetch>(),
}))

/** Builds a full FitnessSeriesRead wire payload, overridable per case. */
function makeSeriesDto(
  overrides: Partial<Schemas['FitnessSeriesRead']> = {},
): Schemas['FitnessSeriesRead'] {
  return {
    method: 'coggan_tss',
    acute_days: 7,
    chronic_days: 42,
    sport: null,
    points: [
      { date: '2026-10-01', load: 120, acute: 16.8, chronic: 2.84, balance: -13.96 },
      { date: '2026-10-02', load: 0, acute: 15.1, chronic: 2.6, balance: -12.5 },
    ],
    ...overrides,
  }
}

/** Builds a full AnalysisSettingsRead wire payload, overridable per case. */
function makeSettingsDto(
  overrides: Partial<Schemas['AnalysisSettingsRead']> = {},
): Schemas['AnalysisSettingsRead'] {
  return {
    acute_days: 7,
    chronic_days: 42,
    primary_method: 'coggan_tss',
    power_thresholds: {
      run: { value_w: 300, w_prime_j: 15000, source: 'auto', cv_pct: 3.2 },
    },
    hr_threshold: { value_bpm: 165, source: 'manual' },
    method_params: { trimp_coefficient: 1.92 },
    ...overrides,
  }
}

describe('analysis service mappers', () => {
  it('maps every fitness-series field to the domain model', () => {
    const series = mapFitnessSeries(makeSeriesDto({ sport: 'run' }))
    expect(series.method).toBe('coggan_tss')
    expect(series.acuteDays).toBe(7)
    expect(series.chronicDays).toBe(42)
    expect(series.sport).toBe('run')
    expect(series.points).toHaveLength(2)
    expect(series.points[1]).toEqual({
      date: '2026-10-02',
      load: 0,
      acute: 15.1,
      chronic: 2.6,
      balance: -12.5,
    })
  })

  it('maps settings including optional thresholds and params', () => {
    const settings = mapSettings(makeSettingsDto())
    expect(settings.powerThresholds.run).toEqual({
      valueW: 300,
      wPrimeJ: 15000,
      source: 'auto',
      cvPct: 3.2,
    })
    expect(settings.hrThreshold).toEqual({ valueBpm: 165, source: 'manual' })
    expect(settings.methodParams).toEqual({ trimp_coefficient: 1.92 })
  })

  it('tolerates a settings payload without thresholds or params', () => {
    const settings = mapSettings(
      makeSettingsDto({
        power_thresholds: undefined,
        hr_threshold: undefined,
        method_params: undefined,
      }),
    )
    expect(settings.powerThresholds).toEqual({})
    expect(settings.hrThreshold).toBeNull()
    expect(settings.methodParams).toEqual({})
    expect(settings.restingHr).toBeNull()
  })

  it('treats a missing threshold source as derived', () => {
    const dto = makeSettingsDto()
    dto.hr_threshold = { value_bpm: 160 } as unknown as Schemas['HeartRateThresholdRead']
    expect(mapSettings(dto).hrThreshold?.source).toBe('auto')
  })
})

describe('fetchFitnessSeries', () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset()
  })

  it('requests the bare path when no filters are given', async () => {
    vi.mocked(apiFetch).mockResolvedValue(makeSeriesDto())
    await fetchFitnessSeries({} satisfies FitnessQuery)
    expect(apiFetch).toHaveBeenCalledWith(
      '/analysis/fitness',
      expect.objectContaining({ signal: undefined }),
    )
  })

  it('translates every filter to its query parameter', async () => {
    vi.mocked(apiFetch).mockResolvedValue(makeSeriesDto())
    await fetchFitnessSeries({
      method: 'hr_load',
      acuteDays: 5,
      chronicDays: 45,
      sport: 'bike',
      fromDate: '2026-07-01',
      toDate: '2026-10-01',
    })
    const url: string = vi.mocked(apiFetch).mock.calls[0]?.[0] as string
    const params = new URLSearchParams(url.split('?')[1])
    expect(params.get('method')).toBe('hr_load')
    expect(params.get('acute_days')).toBe('5')
    expect(params.get('chronic_days')).toBe('45')
    expect(params.get('sport')).toBe('bike')
    expect(params.get('from')).toBe('2026-07-01')
    expect(params.get('to')).toBe('2026-10-01')
  })
})

describe('analysis writes', () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset()
  })

  it('puts the partial payload to the settings route and maps the response', async () => {
    vi.mocked(apiFetch).mockResolvedValue(makeSettingsDto({ acute_days: 10 }))
    const settings = await updateAnalysisSettings({ acute_days: 10 })
    expect(apiFetch).toHaveBeenCalledWith('/analysis/settings', {
      method: 'PUT',
      body: JSON.stringify({ acute_days: 10 }),
    })
    expect(settings.acuteDays).toBe(10)
  })

  it('queues the threshold refresh with a POST', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ queued: true })
    await queueThresholdRefresh()
    expect(apiFetch).toHaveBeenCalledWith('/analysis/thresholds/refresh', {
      method: 'POST',
      body: '{}',
    })
  })

  it('queues the load recompute with a POST', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ queued: true })
    await queueLoadRecompute()
    expect(apiFetch).toHaveBeenCalledWith('/analysis/recompute', { method: 'POST', body: '{}' })
  })

  it('fetches settings from its route', async () => {
    vi.mocked(apiFetch).mockResolvedValue(makeSettingsDto())
    const settings = await fetchAnalysisSettings()
    expect(apiFetch).toHaveBeenCalledWith(
      '/analysis/settings',
      expect.objectContaining({ signal: undefined }),
    )
    expect(settings.chronicDays).toBe(42)
  })
})

describe('power-duration and W-prime mappers', () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset()
  })

  function makePowerDurationDto(
    overrides: Partial<Schemas['PowerDurationRead']> = {},
  ): Schemas['PowerDurationRead'] {
    return {
      sport: 'run',
      points: [{ duration_s: 300, value: 316.7, activity_id: 7 }],
      fit: {
        critical_power_w: 250,
        w_prime_j: 15000,
        cv_pct: 2.5,
        fit_from_s: 300,
        fit_to_s: 1800,
      },
      threshold: { value_w: 250, w_prime_j: 15000, source: 'auto', cv_pct: 2.5 },
      sessions: [{ activity_id: 1, name: 'Morning run', start_time: '2026-10-15T16:00:00+00:00' }],
      overlays: [],
      ...overrides,
    }
  }

  it('maps curve, fit, threshold and sessions', async () => {
    vi.mocked(apiFetch).mockResolvedValue(makePowerDurationDto())
    const duration = await fetchPowerDuration('run', { fitFromS: null, fitToS: null })
    expect(duration.points[0]).toEqual({ durationS: 300, value: 316.7, activityId: 7 })
    expect(duration.fit?.criticalPowerW).toBe(250)
    expect(duration.threshold?.source).toBe('auto')
    expect(duration.sessions[0]?.activityId).toBe(1)
  })

  it('sends the preview window when typed', async () => {
    vi.mocked(apiFetch).mockResolvedValue(makePowerDurationDto({ fit: null, threshold: null }))
    const duration = await fetchPowerDuration('bike', { fitFromS: 120, fitToS: 900 })
    const url: string = vi.mocked(apiFetch).mock.calls[0]?.[0] as string
    const params = new URLSearchParams(url.split('?')[1])
    expect(params.get('sport')).toBe('bike')
    expect(params.get('fit_from')).toBe('120')
    expect(params.get('fit_to')).toBe('900')
    expect(duration.fit).toBeNull()
    expect(duration.threshold).toBeNull()
  })

  it('maps a W-prime balance and fetches it per activity', async () => {
    const dto: Schemas['WprimeBalanceRead'] = {
      cp_w: 250,
      w_prime_j: 15000,
      tau_s: 819.5,
      work_above_cp_j: 4500,
      time_above_cp_s: 60,
      points: [{ t_s: 1, remaining_j: 14750 }],
    }
    vi.mocked(apiFetch).mockResolvedValue(dto)
    const balance = await fetchWprimeBalance(42)
    expect(apiFetch).toHaveBeenCalledWith(
      '/activities/42/wprime',
      expect.objectContaining({ signal: undefined }),
    )
    expect(balance.points[0]).toEqual({ tS: 1, remainingJ: 14750 })
    expect(balance.workAboveCpJ).toBe(4500)
  })

  it('maps an unavailable balance to nulls without failing', async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      cp_w: null,
      w_prime_j: null,
      tau_s: null,
      work_above_cp_j: 0,
      time_above_cp_s: 0,
      points: [],
    })
    const balance = await fetchWprimeBalance(42)
    expect(balance.cpW).toBeNull()
    expect(balance.points).toEqual([])
  })

  it('reads the new fit-window settings fields', () => {
    const dto = makeSettingsDto({ fit_from_s: 120, fit_to_s: 900 })
    const settings = mapSettings(dto)
    expect(settings.fitFromS).toBe(120)
    expect(settings.fitToS).toBe(900)
  })
})

describe('activity interval analysis', () => {
  /** Builds a full ActivityIntervalsRead wire payload. */
  function makeIntervalsDto(
    overrides: Partial<Schemas['ActivityIntervalsRead']> = {},
  ): Schemas['ActivityIntervalsRead'] {
    return {
      shape: 'intervals',
      threshold_w: 350,
      base_power_w: 280,
      rules: {
        open_w: 384,
        close_w: 315,
        min_segment_s: 20,
        short_segment_s: 60,
        short_base: 1.3,
        strong_base: 1.22,
        recovery_w: 279,
        effort_if: 1.05,
        climb_grade_pct: 3.5,
        climb_if: 1.25,
      },
      segments: [
        {
          kind: 'interval',
          start_s: 600,
          duration_s: 180,
          distance_m: 700,
          avg_power_w: 410,
          intensity_factor: 1.17,
          contrast: 1.46,
          avg_grade_pct: 0.4,
          avg_speed_ms: 3.9,
        },
        {
          kind: 'recovery',
          start_s: 780,
          duration_s: 120,
          distance_m: 300,
          avg_power_w: 120,
          intensity_factor: 0.34,
          contrast: 0.43,
          avg_grade_pct: null,
          avg_speed_ms: null,
        },
      ],
      ...overrides,
    }
  }

  it('fetches the breakdown from the activity route and maps every field', async () => {
    vi.mocked(apiFetch).mockResolvedValue(makeIntervalsDto())
    const intervals = await fetchActivityIntervals(42)
    expect(apiFetch).toHaveBeenCalledWith(
      '/activities/42/intervals',
      expect.objectContaining({ signal: undefined }),
    )
    expect(intervals.shape).toBe('intervals')
    expect(intervals.thresholdW).toBe(350)
    expect(intervals.basePowerW).toBe(280)
    expect(intervals.rules?.openW).toBe(384)
    expect(intervals.rules?.effortIf).toBe(1.05)
    expect(intervals.segments[0]).toEqual({
      kind: 'interval',
      startS: 600,
      durationS: 180,
      distanceM: 700,
      avgPowerW: 410,
      intensityFactor: 1.17,
      contrast: 1.46,
      avgGradePct: 0.4,
      avgSpeedMs: 3.9,
    })
  })

  it('keeps absent grade and speed as null so the table can dash them', () => {
    const mapped = mapActivityIntervals(makeIntervalsDto())
    expect(mapped.segments[1]?.avgGradePct).toBeNull()
    expect(mapped.segments[1]?.avgSpeedMs).toBeNull()
  })

  it('maps an activity without power to a steady, empty timeline', async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      shape: 'steady',
      threshold_w: null,
      base_power_w: 0,
      segments: [],
    })

    const intervals = await fetchActivityIntervals(7)
    expect(intervals.shape).toBe('steady')
    expect(intervals.thresholdW).toBeNull()
    expect(intervals.rules).toBeNull()
    expect(intervals.segments).toEqual([])
  })
})

describe('best-splits table', () => {
  it('fetches a window and maps every record field', async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      sport: 'run',
      months: 90,
      records: [
        {
          distance_m: 5000,
          time_s: 1210.5,
          avg_power_w: 392.4,
          activity_id: 21,
          start_time: '2026-09-20T08:00:00+00:00',
        },
      ],
    })
    const table = await fetchBestTimes(90)
    const url: string = vi.mocked(apiFetch).mock.calls[0]?.[0] as string
    const params = new URLSearchParams(url.split('?')[1])
    expect(params.get('sport')).toBe('run')
    expect(params.get('months')).toBe('90')
    expect(table.records[0]).toEqual({
      distanceM: 5000,
      timeS: 1210.5,
      avgPowerW: 392.4,
      activityId: 21,
      startTime: '2026-09-20T08:00:00+00:00',
    })
  })

  it('omits the months parameter for the whole history', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ sport: 'run', months: null, records: [] })
    const table = await fetchBestTimes(null)
    const url: string = vi.mocked(apiFetch).mock.calls[0]?.[0] as string
    expect(url).not.toContain('months=')
    expect(table.months).toBeNull()
    expect(table.records).toEqual([])
  })
})
