import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { SegmentDetailDto, SegmentDetail } from '@/features/segments/types'

import { apiFetch } from '@/services/http'
import {
  deleteSegment,
  fetchSegmentEfforts,
  fetchSegments,
  importSegment,
  mapSegmentDetail,
} from '@/features/segments/services/segments'
import {
  looksLikeGpx,
  parseSegmentGpx,
  parseSegmentJson,
  toImportInput,
} from '@/features/segments/utils/importParsing'
import { buildElevationProfile, cumulativeDistancesMeters } from '@/features/segments/utils/profile'
import {
  formatEffortDuration,
  formatSegmentDistance,
  formatSegmentSpeed,
} from '@/features/segments/utils/format'

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

/** A straight 21-point polyline, ~42 m between points (0.0005 deg of longitude at 41 N). */
function straightGeometry(count = 21): [number, number][] {
  return Array.from({ length: count }, (_, index) => [41.0, 2.0 + index * 0.0005])
}

function makeDetail(overrides: Partial<SegmentDetail> = {}): SegmentDetail {
  return {
    id: 1,
    name: 'Up the hill',
    source: 'strava',
    sourceId: '39270619',
    distanceM: 840,
    elevationGainM: 60,
    createdBy: 2,
    geometry: straightGeometry(),
    altitudes: Array.from({ length: 21 }, (_, index) => 100 + index * 3),
    ...overrides,
  }
}

beforeEach(() => {
  vi.mocked(apiFetch).mockReset()
})

describe('parseSegmentJson', () => {
  it('reads the API import shape', () => {
    const outcome = parseSegmentJson(
      JSON.stringify({
        segment_id: 39270619,
        name: 'Final del Puig Vicenç',
        latlng: straightGeometry(3),
      }),
    )

    expect(outcome.ok).toBe(true)
    if (!outcome.ok) {
      return
    }
    expect(outcome.parsed.segmentId).toBe(39270619)
    expect(outcome.parsed.name).toBe('Final del Puig Vicenç')
    expect(outcome.parsed.latlng).toHaveLength(3)
    expect(outcome.parsed.source).toBe('strava')
  })

  it('reads the raw stream shape the extraction script writes', () => {
    const outcome = parseSegmentJson(
      JSON.stringify({
        latlng: straightGeometry(4),
        altitude: [10, 12, 14, 16],
        distance: [0, 42, 84, 126],
      }),
    )

    expect(outcome.ok).toBe(true)
    if (!outcome.ok) {
      return
    }
    expect(outcome.parsed.segmentId).toBeNull()
    expect(outcome.parsed.altitude).toEqual([10, 12, 14, 16])
    expect(outcome.parsed.distance).toEqual([0, 42, 84, 126])
  })

  it('rejects text that is not JSON', () => {
    const outcome = parseSegmentJson('latlng = [[41, 2]]')

    expect(outcome.ok).toBe(false)
    if (outcome.ok) {
      return
    }
    expect(outcome.error.key).toBe('segments.import.badJson')
  })

  it('rejects a polyline shorter than two points', () => {
    const outcome = parseSegmentJson(JSON.stringify({ latlng: [[41, 2]] }))

    expect(outcome.ok).toBe(false)
    if (outcome.ok) {
      return
    }
    expect(outcome.error.key).toBe('segments.import.tooFewPoints')
  })

  it('rejects altitude arrays that do not match the polyline', () => {
    const outcome = parseSegmentJson(
      JSON.stringify({ latlng: straightGeometry(4), altitude: [10, 12] }),
    )

    expect(outcome.ok).toBe(false)
    if (outcome.ok) {
      return
    }
    expect(outcome.error.key).toBe('segments.import.lengthMismatch')
  })
})

describe('parseSegmentGpx', () => {
  const gpx = `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="Strava" xmlns="http://www.topografix.com/GPX/1/1">
  <trk>
    <name>Camperola</name>
    <trkseg>
      <trkpt lat="41.0" lon="2.0"><ele>100</ele></trkpt>
      <trkpt lat="41.0" lon="2.0005"><ele>110</ele></trkpt>
      <trkpt lat="41.0" lon="2.001"><ele>105</ele></trkpt>
    </trkseg>
  </trk>
</gpx>`

  it('reads track points, altitudes and the track name', () => {
    const outcome = parseSegmentGpx(gpx)

    expect(outcome.ok).toBe(true)
    if (!outcome.ok) {
      return
    }
    expect(outcome.parsed.name).toBe('Camperola')
    expect(outcome.parsed.latlng).toHaveLength(3)
    expect(outcome.parsed.altitude).toEqual([100, 110, 105])
    // A GPX carries no external id, so the form has to supply one.
    expect(outcome.parsed.segmentId).toBeNull()
    expect(outcome.parsed.source).toBe('manual')
  })

  it('rejects a document that is not GPX', () => {
    const outcome = parseSegmentGpx('<html><body>nope</body></html>')

    expect(outcome.ok).toBe(false)
    if (outcome.ok) {
      return
    }
    expect(outcome.error.key).toBe('segments.import.badGpx')
  })

  it('detects the file kinds the picker accepts', () => {
    expect(looksLikeGpx('Segment.GPX')).toBe(true)
    expect(looksLikeGpx('stream_raw.json')).toBe(false)
  })
})

describe('toImportInput', () => {
  const parsed = {
    name: 'From file',
    segmentId: null,
    latlng: straightGeometry(3),
    altitude: null,
    distance: null,
    source: 'manual' as const,
  }

  it('takes the name and id from the form when the file has none', () => {
    const built = toImportInput(parsed, { name: 'Typed name', segmentId: '42' })

    expect(built.ok).toBe(true)
    if (!built.ok) {
      return
    }
    expect(built.input.name).toBe('Typed name')
    expect(built.input.segmentId).toBe(42)
  })

  it('keeps the id the file carried', () => {
    const built = toImportInput({ ...parsed, segmentId: 7071770 }, { name: '', segmentId: '1' })

    expect(built.ok).toBe(true)
    if (!built.ok) {
      return
    }
    expect(built.input.name).toBe('From file')
    expect(built.input.segmentId).toBe(7071770)
  })

  it('asks for an id when neither file nor form has one', () => {
    const built = toImportInput(parsed, { name: 'x', segmentId: '' })

    expect(built.ok).toBe(false)
    if (built.ok) {
      return
    }
    expect(built.error.key).toBe('segments.import.needId')
  })
})

describe('elevation profile', () => {
  it('accumulates distance along the polyline', () => {
    const totals = cumulativeDistancesMeters(straightGeometry(3))

    expect(totals[0]).toBe(0)
    // 0.0005 deg of longitude at 41 N is about 42 m.
    expect(totals[1]).toBeCloseTo(42, 0)
    expect(totals[2]).toBeCloseTo(84, 0)
  })

  it('plots one point per geometry sample and always ends on the last one', () => {
    const profile = buildElevationProfile(makeDetail())

    expect(profile).not.toBeNull()
    expect(profile?.[0]).toEqual({ km: 0, meters: 100 })
    expect(profile?.[profile.length - 1]?.meters).toBe(160)
  })

  it('thins a long segment down to the requested sample count', () => {
    const profile = buildElevationProfile(
      makeDetail({
        geometry: straightGeometry(1001),
        altitudes: Array.from({ length: 1001 }, (_, index) => index),
      }),
      50,
    )

    expect(profile?.length).toBeLessThanOrEqual(51)
    expect(profile?.[profile.length - 1]?.meters).toBe(1000)
  })

  it('is null when the segment has no altitudes', () => {
    expect(buildElevationProfile(makeDetail({ altitudes: null }))).toBeNull()
  })
})

describe('segments service', () => {
  it('reads a library page and keeps the total', async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      items: [
        {
          id: 1,
          name: 'Up the hill',
          source: 'strava',
          source_id: '1',
          distance_m: 840,
          elevation_gain_m: null,
          created_by: 2,
          created_at: '2026-10-30T06:00:00Z',
          updated_at: '2026-10-30T06:00:00Z',
          effort_count: 3,
        },
      ],
      total: 7,
      page: 2,
      num_records: 5,
      next: 3,
    })

    const page = await fetchSegments({ page: 2, numRecords: 5 })

    expect(vi.mocked(apiFetch).mock.calls[0]?.[0]).toBe('/segments?page_number=2&num_records=5')
    expect(page.total).toBe(7)
    expect(page.items[0]?.name).toBe('Up the hill')
    expect(page.items[0]?.effortCount).toBe(3)
  })

  it('passes the direction filter when reading efforts', async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      num_records: 5,
      next: null,
    })

    await fetchSegmentEfforts(3, 'all', { page: 1, numRecords: 5 })

    expect(vi.mocked(apiFetch).mock.calls[0]?.[0]).toBe(
      '/segments/3/efforts?page_number=1&num_records=5&direction=all',
    )
  })

  it('posts the import contract and returns the stored detail', async () => {
    const stored: SegmentDetailDto = {
      id: 9,
      name: 'New segment',
      source: 'manual',
      source_id: '42',
      distance_m: 840,
      elevation_gain_m: 60,
      created_by: 2,
      created_at: '2026-10-30T06:00:00Z',
      updated_at: '2026-10-30T06:00:00Z',
      min_lat: 41,
      min_lng: 2,
      max_lat: 41,
      max_lng: 2.01,
      geometry: [[41, 2]],
      altitudes: [100, 103],
    }
    vi.mocked(apiFetch).mockResolvedValue(stored)

    const detail = await importSegment({
      segmentId: 42,
      name: 'New segment',
      latlng: straightGeometry(2),
      altitude: [100, 103],
      distance: null,
      source: 'manual',
    })

    const body = JSON.parse(String(vi.mocked(apiFetch).mock.calls[0]?.[1]?.body))
    expect(body).toMatchObject({ segment_id: 42, name: 'New segment', source: 'manual' })
    expect(body.altitude).toEqual([100, 103])
    expect(body.distance).toBeUndefined()
    expect(detail.altitudes).toEqual([100, 103])
    expect(mapSegmentDetail(stored).geometry[0]).toEqual([41, 2])
  })
  it('deletes a segment with a void DELETE', async () => {
    vi.mocked(apiFetch).mockResolvedValue(undefined)

    await deleteSegment(4)

    expect(apiFetch).toHaveBeenCalledWith('/segments/4', { method: 'DELETE', responseType: 'void' })
  })
})

describe('segment formatting', () => {
  it('formats durations the way the lap and activity tables do', () => {
    expect(formatEffortDuration(732)).toBe('12:12')
    expect(formatEffortDuration(3725)).toBe('1:02:05')
    expect(formatEffortDuration(null)).toBe('--')
  })

  it('formats length and speed for the viewer units', () => {
    expect(formatSegmentDistance(1947.2, 'metric')).toBe('1.95 km')
    expect(formatSegmentDistance(1947.2, 'imperial')).toBe('1.21 mi')
    expect(formatSegmentSpeed(2.66, 'metric')).toBe('9.6 km/h')
    expect(formatSegmentSpeed(null, 'metric')).toBe('--')
  })
})
