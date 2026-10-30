import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

import type { ActivitySegment, ActivitySegmentDto, Segment } from '@/features/activities/types'

import { apiFetch } from '@/services/http'
import i18n from '@/i18n'
import ActivitySegmentsTable from '@/features/activities/components/ActivitySegmentsTable.vue'
import {
  fetchActivitySegments,
  mapActivitySegment,
} from '@/features/activities/services/activities'

vi.mock('@/services/http', () => {
  class HttpError extends Error {
    constructor(
      readonly status: number,
      statusText: string,
      readonly detail: unknown = null,
    ) {
      super(`HTTP ${status} ${statusText}`)
      this.name = 'HttpError'
    }
  }
  return { apiFetch: vi.fn<typeof apiFetch>(), HttpError }
})

const segmentDto: ActivitySegmentDto = {
  segment: {
    id: 3,
    name: 'Final del Puig Vicenç',
    source: 'strava',
    source_id: '39270619',
    distance_m: 1947.2,
    elevation_gain_m: 154,
    created_by: 2,
    created_at: '2026-10-30T06:00:00Z',
    updated_at: '2026-10-30T06:00:00Z',
  },
  efforts: [
    {
      segment_id: 3,
      activity_id: 5,
      direction: 'forward',
      pass_number: 1,
      elapsed_seconds: 732,
      avg_speed_mps: 2.66,
      coverage: 0.887,
      time_start: '2026-10-15T16:57:00',
      time_end: '2026-10-15T17:09:12',
      stream_index_start: 100,
      stream_index_end: 800,
    },
    {
      segment_id: 3,
      activity_id: 5,
      direction: 'reverse',
      pass_number: 2,
      elapsed_seconds: 796,
      avg_speed_mps: 2.45,
      coverage: 0.874,
      time_start: '2026-10-15T17:20:00',
      time_end: '2026-10-15T17:33:16',
      stream_index_start: 900,
      stream_index_end: 1600,
    },
  ],
}

/** Builds the domain shape the table consumes, from the fixture DTO. */
function makeSegments(overrides: Partial<Segment> = {}): ActivitySegment[] {
  const mapped = mapActivitySegment(segmentDto)
  return [{ ...mapped, segment: { ...mapped.segment, ...overrides } }]
}

function mountTable(segments: ActivitySegment[]) {
  return mount(ActivitySegmentsTable, {
    props: { segments, activityType: 1, units: 'metric' },
    global: { plugins: [i18n] },
  })
}

/** Returns one body row of the rendered table, failing loudly when absent. */
function rowAt(wrapper: ReturnType<typeof mountTable>, index: number) {
  const row = wrapper.findAll('tbody tr')[index]
  if (!row) {
    throw new Error(`no segment row at index ${index}`)
  }
  return row
}

beforeEach(() => {
  vi.mocked(apiFetch).mockReset()
})

describe('mapActivitySegment', () => {
  it('maps the segment and its efforts to the clean model', () => {
    const mapped = mapActivitySegment(segmentDto)

    expect(mapped.segment).toEqual({
      id: 3,
      name: 'Final del Puig Vicenç',
      source: 'strava',
      distanceM: 1947.2,
      elevationGainM: 154,
    })
    expect(mapped.efforts).toHaveLength(2)
    expect(mapped.efforts[0]).toMatchObject({
      segmentId: 3,
      activityId: 5,
      direction: 'forward',
      passNumber: 1,
      elapsedSeconds: 732,
      avgSpeedMps: 2.66,
    })
  })

  it('keeps a segment with no known gain nullable', () => {
    const mapped = mapActivitySegment({
      ...segmentDto,
      segment: { ...segmentDto.segment, elevation_gain_m: null },
    })

    expect(mapped.segment.elevationGainM).toBeNull()
  })
})

describe('fetchActivitySegments', () => {
  it('reads the authenticated activity route only', async () => {
    vi.mocked(apiFetch).mockResolvedValue([segmentDto])

    const segments = await fetchActivitySegments(5, { authenticated: true })

    expect(apiFetch).toHaveBeenCalledWith('/activities/5/segments', {
      auth: true,
      signal: undefined,
    })
    expect(segments).toHaveLength(1)
    expect(segments[0]?.efforts).toHaveLength(2)
  })

  it('treats a null body as no segments', async () => {
    vi.mocked(apiFetch).mockResolvedValue(null)

    expect(await fetchActivitySegments(5, { authenticated: true })).toEqual([])
  })
})

describe('ActivitySegmentsTable', () => {
  it('renders one row per pass with the effort time', () => {
    const wrapper = mountTable(makeSegments())

    expect(wrapper.findAll('tbody tr')).toHaveLength(2)
    expect(rowAt(wrapper, 0).text()).toContain('Final del Puig Vicenç')
    // 732 s and 796 s, both under an hour.
    expect(rowAt(wrapper, 0).text()).toContain('12:12')
    expect(rowAt(wrapper, 1).text()).toContain('13:16')
  })

  it('shows the segment length and gain', () => {
    const text = mountTable(makeSegments()).text()

    expect(text).toContain('1.95 km')
    expect(text).toContain('154')
  })

  it('omits the gain block when the segment has none', () => {
    const firstRow = rowAt(mountTable(makeSegments({ elevationGainM: null })), 0)

    expect(firstRow.text()).toContain('1.95 km')
    expect(firstRow.text()).not.toContain('·')
  })

  it('labels a reverse pass without hiding it', () => {
    const wrapper = mountTable(makeSegments())

    expect(rowAt(wrapper, 0).text()).not.toContain('Reverse')
    expect(rowAt(wrapper, 1).text()).toContain('Reverse')
    expect(rowAt(wrapper, 1).classes()).toContain('opacity-60')
  })

  it('numbers only the extra passes in a direction', () => {
    const wrapper = mountTable(makeSegments())

    expect(rowAt(wrapper, 0).text()).not.toContain('Pass')
    expect(rowAt(wrapper, 1).text()).toContain('Pass 2')
  })

  it('renders a headed table for an activity that has segments', () => {
    const text = mountTable(makeSegments()).text()

    expect(text).toContain('Segment')
    expect(text).toContain('Length')
    expect(text).toContain('Time')
  })
})
