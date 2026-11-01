import type { Schemas } from '@/types'

/**
 * Wire shapes from the generated OpenAPI schema, kept as the boundary types so a
 * backend contract change surfaces here as a TypeScript error rather than a
 * runtime mismatch. The mappers in `services/segments.ts` translate them into the
 * camelCase domain models below.
 */
export type SegmentSummaryDto = Schemas['SegmentListItemRead']
export type SegmentBaseDto = Schemas['SegmentRead']
export type SegmentDetailDto = Schemas['SegmentDetailRead']
export type SegmentEffortDto = Schemas['SegmentEffortRead']
export type SegmentImportPayload = Schemas['SegmentImport']

/** The fields every stored segment carries, listed or detailed. */
export interface SegmentBase {
  id: number
  name: string
  /** Definition origin (`strava` or `manual`). */
  source: string
  /** External identifier within the source (the Strava segment id). */
  sourceId: string | null
  /** Length in metres along the stored geometry order. */
  distanceM: number
  /** Positive elevation gain in metres, when the source provided it. */
  elevationGainM: number | null
  /** Positive elevation dropped along the geometry; null when the source gave no altitudes. */
  elevationLossM: number | null
  /** User that imported or created the definition. */
  createdBy: number | null
}

/** A stored segment as the library list shows it. */
export interface SegmentSummary extends SegmentBase {
  /** How many of the reader's activities have a counted effort on this segment. */
  effortCount: number
}

/** The full definition, with what the map and the profile need to draw. */
export interface SegmentDetail extends SegmentBase {
  /** Ordered `[[lat, lng], ...]` polyline; index 0 is the canonical start. */
  geometry: [number, number][]
  /** Per-point altitude in metres, index-aligned with `geometry`. */
  altitudes: number[] | null
}

/** One pass of one of the viewer's activities over a segment. */
export interface SegmentEffort {
  segmentId: number
  activityId: number
  /** `forward` counts as an effort; `reverse` runs the geometry backwards. */
  direction: string
  /** 1-based pass index within (segment, direction). */
  passNumber: number
  /** Effort duration in seconds. */
  elapsedSeconds: number
  /** Segment distance over elapsed time, in metres per second. */
  avgSpeedMps: number | null
  /** Sport of the effort's activity, so the table shows pace or speed as elsewhere. */
  activityType: number | null
  /** Mean power over the effort's window; null without a power stream. */
  avgPowerW: number | null
  /** Mean heart rate over the effort's window; null without a heart-rate stream. */
  avgHrBpm: number | null
  /** The shared aRSS score per minute over the effort; null without power or threshold. */
  rssPerMin: number | null
  /** Wall-clock time of the first effort waypoint (ISO). */
  timeStart: string
  /** Wall-clock time of the last effort waypoint (ISO). */
  timeEnd: string
}

/** One page of the segment library. */
export interface SegmentsPage {
  items: SegmentSummary[]
  total: number
}

/** One page of a segment's efforts. */
export interface SegmentEffortsPage {
  items: SegmentEffort[]
  total: number
}

/** Which directions an effort list should contain. */
export type EffortDirectionFilter = 'forward' | 'reverse' | 'all'

/** What the import form submits, before it becomes a `SegmentImportPayload`. */
export interface SegmentImportInput {
  /** External id within the source (a Strava segment id, or a chosen number). */
  segmentId: number
  name: string
  latlng: [number, number][]
  altitude: number[] | null
  /** Cumulative distance per point; the server uses the last value as length. */
  distance: number[] | null
  source: 'strava' | 'manual'
}
