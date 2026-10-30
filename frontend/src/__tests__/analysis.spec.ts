import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Schemas } from '@/types'
import type { FitnessQuery } from '@/features/analysis/types'

import { apiFetch } from '@/services/http'
import {
  fetchAnalysisSettings,
  fetchFitnessSeries,
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
