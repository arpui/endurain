import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Schemas } from '@/types'
import type { FitnessQuery } from '@/features/analysis/types'

import { apiFetch } from '@/services/http'
import {
  fetchActivityIntervals,
  fetchAnalysisSettings,
  fetchBestTimes,
  fetchCpTrend,
  fetchFitnessSeries,
  fetchPowerDuration,
  fetchPowerFlags,
  fetchWprimeBalance,
  mapActivityIntervals,
  mapFitnessSeries,
  mapSettings,
  queueLoadRecompute,
  queueThresholdRefresh,
  connectRoutePoints,
  createRoute,
  deleteRoute,
  fetchRoutes,
  fetchSegmentNetwork,
  fetchTrailNetwork,
  fetchTrailSections,
  fetchTrailVariants,
  postVariantMark,
  updateRoute,
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

  /** Builds a full CpTrendRead wire payload, overridable per case. */
  function makeCpTrendDto(overrides: Partial<Schemas['CpTrendRead']> = {}): Schemas['CpTrendRead'] {
    return {
      sport: 'run',
      window_days: null,
      measure: 'power',
      threshold: { value_w: 425.2, w_prime_j: 21000, source: 'auto', cv_pct: 4.1 },
      points: [
        {
          activity_id: 1,
          date: '2026-01-10',
          cp_w: 380,
          w_prime_j: 20000,
          cv_pct: 5,
          power_source: 'stryd',
        },
        {
          activity_id: 2,
          date: '2026-02-10',
          cp_w: 400,
          w_prime_j: 21000,
          cv_pct: 4,
          power_source: null,
        },
      ],
      envelope: [
        { date: '2026-01-10', cp_w: 391.5 },
        { date: '2026-02-10', cp_w: 400 },
      ],
      ...overrides,
    }
  }

  it('maps the temporal-threshold envelope alongside the points', async () => {
    vi.mocked(apiFetch).mockResolvedValue(makeCpTrendDto())
    const trend = await fetchCpTrend('run', null)
    expect(trend.points[0]).toEqual({
      activityId: 1,
      date: '2026-01-10',
      cpW: 380,
      wPrimeJ: 20000,
      cvPct: 5,
      powerSource: 'stryd',
    })
    expect(trend.envelope).toEqual([
      { date: '2026-01-10', cpW: 391.5 },
      { date: '2026-02-10', cpW: 400 },
    ])
  })

  it('answers an empty envelope when the payload carries none', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ ...makeCpTrendDto(), envelope: undefined as never })
    const trend = await fetchCpTrend('run', 365)
    expect(trend.envelope).toEqual([])
    const url: string = vi.mocked(apiFetch).mock.calls[0]?.[0] as string
    expect(new URLSearchParams(url.split('?')[1]).get('window')).toBe('365')
  })

  it('carries the speed measure and its pace-side threshold', async () => {
    vi.mocked(apiFetch).mockResolvedValue(
      makeCpTrendDto({
        measure: 'speed',
        threshold: null,
        speed_threshold: { value_ms: 4.15, w_prime_m: 260, source: 'auto', cv_pct: 3.0 },
      }),
    )
    const trend = await fetchCpTrend('run', null)
    expect(trend.measure).toBe('speed')
    expect(trend.threshold).toBeNull()
    expect(trend.speedThreshold).toEqual({ valueMs: 4.15, wPrimeM: 260, source: 'auto', cvPct: 3 })
  })
})

describe('power-flags fetch', () => {
  it('maps the sessions held out of the curve', async () => {
    vi.mocked(apiFetch).mockResolvedValue([
      {
        activity_id: 1059,
        started_on: '2023-06-25',
        duration_s: 60,
        value_w: 1044.0,
        session_np_w: 345.0,
        ratio: 3.03,
        cap: 1.9,
      },
    ])
    const flags = await fetchPowerFlags('run')
    expect(flags).toEqual([
      {
        activityId: 1059,
        startedOn: '2023-06-25',
        durationS: 60,
        valueW: 1044,
        sessionNpW: 345,
        ratio: 3.03,
        cap: 1.9,
      },
    ])
  })
})

describe('trail technical sections', () => {
  it('maps the sections and turns their points into map coordinates', async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      activity_id: 9,
      threshold: 0.38,
      technical_ratio: 1.3,
      hard_ratio: 1.8,
      very_max_length_m: 200,
      v_base_ms: 2.47,
      cadence_available: true,
      technical_m: 5750.0,
      total_m: 18760.0,
      sections: [
        {
          start_m: 7675.0,
          length_m: 1325.0,
          grade_pct: -14.7,
          avg_speed_ms: 1.52,
          gap_deficit: 0.67,
          cadence_jitter: 0.32,
          brake_score: 0.86,
          score: 0.83,
          direction: 'descent',
          core_start_m: 8100.0,
          core_end_m: 8400.0,
          core_score: 0.91,
          points: [{ lat: 42.47, lon: 0.99 }],
        },
      ],
    } satisfies Schemas['TrailSectionsRead'])
    const sections = await fetchTrailSections(9)
    expect(apiFetch).toHaveBeenCalledWith('/activities/9/trail-sections', expect.anything())
    expect(sections.vBaseMs).toBe(2.47)
    expect(sections.threshold).toBe(0.38)
    expect(sections.hardRatio).toBe(1.8)
    expect(sections.technicalM).toBe(5750)
    expect(sections.sections[0]).toEqual({
      startM: 7675,
      lengthM: 1325,
      gradePct: -14.7,
      avgSpeedMs: 1.52,
      gapDeficit: 0.67,
      cadenceJitter: 0.32,
      brakeScore: 0.86,
      score: 0.83,
      direction: 'descent',
      coreStartM: 8100,
      coreEndM: 8400,
      coreScore: 0.91,
      points: [{ lat: 42.47, lng: 0.99 }],
      osm: null,
      mark: null,
    })
  })

  it('keeps the cartographic claim beside the measured one, as OSM states it', async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      activity_id: 9,
      threshold: 0.38,
      technical_ratio: 1.3,
      hard_ratio: 1.8,
      very_max_length_m: 200,
      v_base_ms: 2.46,
      cadence_available: true,
      technical_m: 1225.0,
      total_m: 18693.8,
      sections: [
        {
          start_m: 7675.0,
          length_m: 1225.0,
          grade_pct: -15.3,
          avg_speed_ms: 1.51,
          gap_deficit: 0.68,
          cadence_jitter: 0.36,
          brake_score: 0.81,
          score: 0.83,
          direction: 'descent',
          core_start_m: 8100.0,
          core_end_m: 8400.0,
          core_score: 0.91,
          points: [],
          osm: {
            way_id: 4242,
            name: 'Camí de la Teula',
            sac_scale: 'T3',
            surface: 'rock',
            trail_visibility: 'no',
            start_m: 300.0,
            end_m: 1520.0,
            covered_m: 1220.0,
            cache_date: '2026-11-01',
          },
        },
      ],
    } satisfies Schemas['TrailSectionsRead'])
    const [section] = (await fetchTrailSections(9)).sections
    expect(section?.score).toBe(0.83)
    expect(section?.osm).toMatchObject({
      wayId: 4242,
      name: 'Camí de la Teula',
      sacScale: 'T3',
      surface: 'rock',
      trailVisibility: 'no',
      mtbScale: null,
      cacheDate: '2026-11-01',
    })
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
      climbs: [],
      rss_by_source: [],
      rules: {
        open_w: 384,
        close_w: 315,
        min_segment_s: 20,
        short_segment_s: 60,
        short_base: 1.3,
        strong_base: 1.22,
        recovery_w: 279,
        effort_if: 1.05,
        tempo_open_w: 315,
        tempo_min_s: 120,
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
          pace_adjusted_ms: 4.4,
          elevation_gain_m: 9.5,
          elevation_loss_m: 4,
          rss: 12.3,
          rss_per_min: 17.6,
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
          elevation_gain_m: null,
          elevation_loss_m: null,
          rss: null,
          rss_per_min: null,
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
      paceAdjustedMs: 4.4,
      elevationGainM: 9.5,
      elevationLossM: 4,
      rss: 12.3,
      rssPerMin: 17.6,
    })
    expect(intervals.climbs).toEqual([])
  })

  it('keeps absent grade and speed as null so the table can dash them', () => {
    const mapped = mapActivityIntervals(makeIntervalsDto())
    expect(mapped.segments[1]?.avgGradePct).toBeNull()
    expect(mapped.segments[1]?.avgSpeedMs).toBeNull()
    expect(mapped.segments[1]?.elevationGainM).toBeNull()
    expect(mapped.segments[1]?.rss).toBeNull()
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

describe('trail network', () => {
  it('maps the crossed ways, their bands and their geometry', async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      method: 'trail:2',
      threshold: 0.38,
      technical_ratio: 1.3,
      hard_ratio: 2.0,
      min_descents: 3,
      ways: [
        {
          way_id: 4242,
          name: 'Corriol',
          claim: 'hard',
          crossings_total: 6,
          descent_crossings: 4,
          ascent_crossings: 1,
          hard_crossings: 3,
          med_score: 0.84,
          q1: 0.8,
          q3: 0.87,
          min_score: 0.7,
          max_score: 0.9,
          med_grade_pct: -19,
          length_m: 1800,
          geometry: [
            [42.47, 0.99],
            [42.471, 0.991],
          ],
          sac_scale: 'demanding_mountain_hiking',
          mtb_scale: null,
          surface: 'rock',
          tracktype: null,
        },
      ],
    })
    const network = await fetchTrailNetwork()
    expect(String(vi.mocked(apiFetch).mock.calls[0]?.[0])).toContain(
      '/analysis/trail-network?limit=5000',
    )
    expect(network.minDescents).toBe(3)
    expect(network.hardRatio).toBe(2.0)
    expect(network.ways[0]).toMatchObject({
      wayId: 4242,
      claim: 'hard',
      descentCrossings: 4,
      medScore: 0.84,
    })
    expect(network.ways[0]?.points).toEqual([
      { lat: 42.47, lng: 0.99 },
      { lat: 42.471, lng: 0.991 },
    ])
    expect(network.ways[0]?.sacScale).toBe('demanding_mountain_hiking')
    expect(network.ways[0]?.tracktype).toBeNull()
  })
})

describe('segment network (named ground)', () => {
  it('maps named segments with their claims and geometry', async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      min_descents: 3,
      threshold: 0.62,
      technical_ratio: 1.3,
      hard_ratio: 2.0,
      segments: [
        {
          segment_id: 12,
          name: 'PistaHN 400m',
          claim: 'technical',
          length_m: 409,
          gain_m: 9,
          crossings_total: 485,
          descent_crossings: 12,
          med_score: 1.0,
          q1: 0.9,
          q3: 1.1,
          med_grade_pct: -7.5,
          loop: false,
          geometry: [
            [42.4, 0.9],
            [42.41, 0.91],
          ],
        },
      ],
    } satisfies Schemas['SegmentNetworkRead'])
    const net = await fetchSegmentNetwork(false)
    expect(String(vi.mocked(apiFetch).mock.calls[0]?.[0])).toContain(
      '/analysis/segments-network?show_loops=false',
    )
    expect(net.segments[0]).toMatchObject({
      segmentId: 12,
      name: 'PistaHN 400m',
      claim: 'technical',
      descentCrossings: 12,
      loop: false,
    })
    expect(net.segments[0]?.points[1]).toEqual({ lat: 42.41, lng: 0.91 })
  })
})

describe('trail variant analysis', () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset()
  })

  it('maps the wire payload to junction-keyed variants', async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      activity_id: 2044,
      cell_m: 15,
      variants: [
        {
          split_m: 4200,
          rejoin_m: 5300,
          side: 'left',
          variant_m: 1180,
          main_m: 1100,
          delta_m: 80,
          delta_gain_m: -15,
          status: 'confirmada',
          grade: 'tech',
          path_latlng: [
            [42.47, 0.99],
            [42.471, 0.991],
          ],
          mark: 'falsa',
        },
      ],
    } satisfies Schemas['TrailVariantsRead'])
    const variants = await fetchTrailVariants(2044)
    expect(String(vi.mocked(apiFetch).mock.calls[0]?.[0])).toContain(
      '/activities/2044/trail-variants',
    )
    expect(variants.cellM).toBe(15)
    expect(variants.variants[0]).toMatchObject({
      splitM: 4200,
      rejoinM: 5300,
      side: 'left',
      status: 'confirmada',
      mark: 'falsa',
    })
    expect(variants.variants[0]?.points).toEqual([
      { lat: 42.47, lng: 0.99 },
      { lat: 42.471, lng: 0.991 },
    ])
  })

  it('judges a variant by junction, and takes it back with a delete', async () => {
    vi.mocked(apiFetch).mockResolvedValue(undefined)
    await postVariantMark(2044, 4200, 'real')
    expect(apiFetch).toHaveBeenCalledWith('/activities/2044/trail-variants/marks', {
      method: 'POST',
      signal: undefined,
      body: JSON.stringify({ split_m: 4200, verdict: 'real' }),
    })
    await postVariantMark(2044, 4200, null)
    const lastCall = vi.mocked(apiFetch).mock.calls[vi.mocked(apiFetch).mock.calls.length - 1]
    expect(String(lastCall?.[0])).toContain('/activities/2044/trail-variants/marks?split_m=4200')
    expect(lastCall?.[1]).toMatchObject({ method: 'DELETE' })
  })
})

describe('designed routes', () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset()
  })

  it('maps saved routes from the wire geometry pairs', async () => {
    vi.mocked(apiFetch).mockResolvedValue([
      {
        id: 7,
        name: 'Casa → coll',
        geometry: [
          [42.47, 1.5],
          [42.48, 1.51],
        ],
        distance_m: 3120.5,
        gain_m: 210,
        notes: 'experimental',
        created_at: '2026-11-06T20:00:00+00:00',
      },
    ] satisfies Schemas['RouteRead'][])
    const routes = await fetchRoutes()
    expect(routes[0]).toMatchObject({ id: 7, name: 'Casa → coll', distanceM: 3120.5, gainM: 210 })
    expect(routes[0]?.points[1]).toEqual({ lat: 42.48, lng: 1.51 })
  })

  it('posts the polyline as [lat, lng] pairs and deletes by id', async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      id: 8,
      name: 'vora',
      geometry: [
        [42.47, 1.5],
        [42.48, 1.51],
      ],
      distance_m: 1400,
      gain_m: 60,
      notes: null,
      created_at: '2026-11-06T20:00:00+00:00',
    } satisfies Schemas['RouteRead'])
    await createRoute('vora', [
      { lat: 42.47, lng: 1.5 },
      { lat: 42.48, lng: 1.51 },
    ])
    const [, init] = vi.mocked(apiFetch).mock.calls[0] ?? []
    expect(init).toMatchObject({ method: 'POST' })
    expect(JSON.parse(String(init?.body))).toMatchObject({
      name: 'vora',
      geometry: [
        [42.47, 1.5],
        [42.48, 1.51],
      ],
    })
    await deleteRoute(8)
    expect(apiFetch).toHaveBeenCalledWith('/analysis/routes/8', {
      method: 'DELETE',
      signal: undefined,
    })
  })

  it('asks the walked connect for a snapped polyline', async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      geometry: [
        [42.3, 1.5],
        [42.3, 1.50018],
      ],
      walked: [true],
    } satisfies Schemas['RouteConnectRead'])
    const res = await connectRoutePoints([
      { lat: 42.3, lng: 1.5 },
      { lat: 42.30001, lng: 1.50019 },
    ])
    expect(apiFetch).toHaveBeenCalledWith('/analysis/routes/connect', {
      method: 'POST',
      signal: undefined,
      body: JSON.stringify({
        points: [
          [42.3, 1.5],
          [42.30001, 1.50019],
        ],
      }),
    })
    expect(res.walked).toEqual([true])
    expect(res.points[1]).toEqual({ lat: 42.3, lng: 1.50018 })
  })

  it('edits a saved route in place with PUT', async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      id: 9,
      name: 'rebaixada',
      geometry: [
        [42.47, 1.5],
        [42.48, 1.51],
      ],
      distance_m: 1200,
      gain_m: 40,
      notes: null,
      created_at: '2026-11-06T20:00:00+00:00',
    } satisfies Schemas['RouteRead'])
    const edited = await updateRoute(9, 'rebaixada', [
      { lat: 42.47, lng: 1.5 },
      { lat: 42.48, lng: 1.51 },
    ])
    const [path, init] = vi.mocked(apiFetch).mock.calls[0] ?? []
    expect(path).toBe('/analysis/routes/9')
    expect(init).toMatchObject({ method: 'PUT' })
    expect(edited).toMatchObject({ id: 9, name: 'rebaixada', gainM: 40 })
  })
})
