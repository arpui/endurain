import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

import type { Schemas } from '@/types'
import type { Activity, ActivityLap } from '@/features/activities/types'
import type { LapDynamics } from '@/features/analysis/types'

import { apiFetch } from '@/services/http'
import i18n from '@/i18n'
import LapDynamicsTable from '@/features/analysis/components/LapDynamicsTable.vue'
import { fetchActivityLapDynamics } from '@/features/analysis/services/analysis'

vi.mock('@/services/http', () => ({
  apiFetch: vi.fn<typeof apiFetch>(),
}))

function makeLap(overrides: Partial<Schemas['ActivityLapsRead']> = {}): ActivityLap {
  return {
    id: 1,
    totalDistance: 1000,
    totalElapsedTime: 250,
    totalTimerTime: 250,
    enhancedAvgPace: 0.25,
    enhancedAvgSpeed: 4,
    totalAscent: 2,
    avgHeartRate: 152,
    avgCadence: 80,
    totalCycles: 330,
    intensity: 'active',
    ...overrides,
  } as unknown as ActivityLap
}

function makeActivity(): Activity {
  return { activityType: 1 } as unknown as Activity
}

describe('lap dynamics fetch', () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset()
  })

  it('fetches the owner route and maps every field', async () => {
    vi.mocked(apiFetch).mockResolvedValue([
      {
        lap_id: 7,
        avg_power_w: 400,
        stride_length_m: 3.0,
        elevation_gain_m: 10,
        elevation_loss_m: 8,
        rss: 26.4,
      },
      {
        lap_id: 8,
        avg_power_w: null,
        stride_length_m: null,
        elevation_gain_m: null,
        elevation_loss_m: null,
        rss: null,
      },
    ] satisfies Schemas['LapDynamicsRead'][])
    const rows = await fetchActivityLapDynamics(21)
    expect(apiFetch).toHaveBeenCalledWith('/activities/21/lap-dynamics', expect.anything())
    expect(rows[0]).toEqual({
      lapId: 7,
      avgPowerW: 400,
      strideLengthM: 3,
      elevationGainM: 10,
      elevationLossM: 8,
      rss: 26.4,
    })
    expect(rows[1]).toEqual({
      lapId: 8,
      avgPowerW: null,
      strideLengthM: null,
      elevationGainM: null,
      elevationLossM: null,
      rss: null,
    })
  })
})

describe('LapDynamicsTable', () => {
  function mountTable(laps: ActivityLap[], dynamics: LapDynamics[]) {
    return mount(LapDynamicsTable, {
      props: { laps, dynamics, activity: makeActivity(), units: 'metric' },
      global: { plugins: [i18n] },
    })
  }

  function cells(wrapper: ReturnType<typeof mountTable>): string[] {
    const row = wrapper.findAll('tbody tr')[0]
    if (!row) {
      throw new Error('no lap row rendered')
    }
    return row.findAll('td').map((cell) => cell.text())
  }

  it('adds power, stride, elevation split and aRSS columns next to the classic ones', () => {
    const row = cells(
      mountTable(
        [makeLap()],
        [
          {
            lapId: 1,
            avgPowerW: 412,
            strideLengthM: 3.03,
            elevationGainM: 10,
            elevationLossM: 8,
            rss: 26.4,
          },
        ],
      ),
    )
    expect(row).toContain('412 W')
    expect(row).toContain('+10 / \u22128 m')
    expect(row.some((value) => /^26[.,]4$/.test(value))).toBe(true)
    expect(row.some((value) => /^3[.,]03$/.test(value))).toBe(true)
  })

  it('hides the analysis columns when the device recorded nothing to derive', () => {
    const wrapper = mountTable(
      [makeLap()],
      [
        {
          lapId: 1,
          avgPowerW: null,
          strideLengthM: null,
          elevationGainM: null,
          elevationLossM: null,
          rss: null,
        },
      ],
    )
    const headers = wrapper.findAll('thead th').map((th) => th.text())
    expect(headers.some((h) => /aRSS/i.test(h))).toBe(false)
    expect(wrapper.findAll('tbody tr td').length).toBeLessThan(11)
  })
})
