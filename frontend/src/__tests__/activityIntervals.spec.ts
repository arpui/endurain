import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import type { ActivityIntervals, IntervalSegment } from '@/features/analysis/types'

import i18n from '@/i18n'
import ActivityIntervalTable from '@/features/analysis/components/ActivityIntervalTable.vue'

/** Builds one timeline stretch with sane defaults, overridable per case. */
function makeSegment(overrides: Partial<IntervalSegment> = {}): IntervalSegment {
  return {
    kind: 'interval',
    startS: 600,
    durationS: 180,
    distanceM: 700,
    avgPowerW: 410,
    intensityFactor: 1.17,
    contrast: 1.46,
    avgGradePct: 0.4,
    avgSpeedMs: 3.84,
    elevationGainM: 9.5,
    elevationLossM: 4,
    rss: 12.3,
    rssPerMin: 17.6,
    ...overrides,
  }
}

/** Builds a breakdown whose only row is the given segment. */
function makeIntervals(segment: IntervalSegment): ActivityIntervals {
  return {
    shape: 'intervals',
    thresholdW: 350,
    basePowerW: 280,
    segments: [segment],
    climbs: [],
    rules: {
      openW: 349,
      closeW: 314,
      minSegmentS: 20,
      shortSegmentS: 60,
      shortBase: 1.3,
      strongBase: 1.22,
      recoveryW: 279,
      effortIf: 1.05,
      climbGradePct: 3.5,
      climbIf: 1.25,
    },
  }
}

/** Mounts the table and returns the tempo cell of its first row. */
function tempoCell(segment: IntervalSegment, activityType: number): string {
  const wrapper = mount(ActivityIntervalTable, {
    props: { intervals: makeIntervals(segment), activityType, units: 'metric' },
    global: { plugins: [i18n] },
  })
  const cell = wrapper.findAll('tbody tr')[0]?.findAll('td')[4]
  if (!cell) {
    throw new Error('no interval row rendered')
  }
  return cell.text()
}

describe('ActivityIntervalTable tempo column', () => {
  it('shows min/km for running, derived from the mean speed', () => {
    // 3.84 m/s -> 260 s/km -> 4:20
    expect(tempoCell(makeSegment(), 1)).toBe('4:20 min/km')
  })

  it('shows km/h for cycling from the same mean speed', () => {
    // 3.84 m/s -> 13.82 km/h
    expect(tempoCell(makeSegment(), 4)).toBe('13.82 km/h')
  })

  it('dashes the tempo when the activity has no speed stream', () => {
    expect(tempoCell(makeSegment({ avgSpeedMs: null, distanceM: 0 }), 1)).toBe('--')
  })
})

describe('ActivityIntervalTable terrain climbs', () => {
  function mountWith(climbs: ActivityIntervals['climbs']) {
    return mount(ActivityIntervalTable, {
      props: {
        intervals: { ...makeIntervals(makeSegment()), climbs },
        activityType: 1,
        units: 'metric',
      },
      global: { plugins: [i18n] },
    })
  }

  it('lists the climbs the route shows, with their per-kilometre stress', () => {
    const wrapper = mountWith([
      {
        startM: 100,
        endM: 4200,
        lengthM: 4100,
        startS: 25,
        durationS: 1025,
        elevationGainM: 229,
        elevationLossM: 22,
        avgGradePct: 5.04,
        avgPowerW: 390,
        intensityFactor: 1.11,
        contrast: 1.39,
        rss: 60.2,
        rssPerMin: 14.7,
      },
    ])
    const rows = wrapper.findAll('tbody tr')
    const climbRow = rows[rows.length - 1]
    if (!climbRow) {
      throw new Error('no climb row rendered')
    }
    expect(climbRow.text()).toMatch(/0[.,]1 - 4[.,]2 km/)
    expect(climbRow.text()).toContain('+229')
    expect(climbRow.text()).toContain('14.7')
    expect(climbRow.text()).toContain('390 W')
  })

  it('shows nothing extra when the route has no climbs', () => {
    const wrapper = mountWith([])
    expect(wrapper.findAll('tbody tr').length).toBe(1) // just the timeline row
  })

  it('keeps the power columns dashed for an athlete without a power meter', () => {
    const wrapper = mount(ActivityIntervalTable, {
      props: {
        intervals: {
          shape: 'steady',
          thresholdW: null,
          basePowerW: 0,
          segments: [],
          climbs: [
            {
              startM: 0,
              endM: 1200,
              lengthM: 1200,
              startS: 0,
              durationS: 300,
              elevationGainM: 80,
              elevationLossM: 0,
              avgGradePct: 6.7,
              avgPowerW: null,
              intensityFactor: null,
              contrast: null,
              rss: null,
              rssPerMin: 22.0,
            },
          ],
          rules: null,
        },
        activityType: 1,
        units: 'metric',
      },
      global: { plugins: [i18n] },
    })
    const row = wrapper.findAll('tbody tr')[0]
    if (!row) {
      throw new Error('no climb row rendered')
    }
    expect(row.text()).toContain('--')
    expect(row.text()).toContain('+80')
  })
})
