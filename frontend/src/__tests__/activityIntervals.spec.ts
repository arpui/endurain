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
