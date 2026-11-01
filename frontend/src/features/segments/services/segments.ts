import type {
  EffortDirectionFilter,
  SegmentBase,
  SegmentBaseDto,
  SegmentDetail,
  SegmentDetailDto,
  SegmentEffort,
  SegmentEffortDto,
  SegmentEffortsPage,
  SegmentImportInput,
  SegmentImportPayload,
  SegmentSummary,
  SegmentSummaryDto,
  SegmentsPage,
} from '../types'
import type { Schemas } from '@/types'

import { apiFetch } from '@/services/http'

/** Maps the fields every stored segment DTO carries, list or detail. */
function mapSegmentBase(dto: SegmentBaseDto): SegmentBase {
  return {
    id: dto.id,
    name: dto.name,
    source: dto.source,
    sourceId: dto.source_id ?? null,
    distanceM: dto.distance_m,
    elevationGainM: dto.elevation_gain_m ?? null,
    elevationLossM: dto.elevation_loss_m ?? null,
    createdBy: dto.created_by ?? null,
  }
}

/** Maps a library-list DTO to the domain model, with the reader's effort count. */
export function mapSegmentSummary(dto: SegmentSummaryDto): SegmentSummary {
  return { ...mapSegmentBase(dto), effortCount: dto.effort_count ?? 0 }
}

/** Maps a segment detail DTO (geometry and altitudes included) to the domain model. */
export function mapSegmentDetail(dto: SegmentDetailDto): SegmentDetail {
  return {
    ...mapSegmentBase(dto),
    geometry: (dto.geometry ?? []).map((point) => [point[0] ?? 0, point[1] ?? 0]),
    altitudes: dto.altitudes ?? null,
  }
}

/** Maps one stored effort to the domain model. */
export function mapSegmentEffort(dto: SegmentEffortDto): SegmentEffort {
  return {
    segmentId: dto.segment_id,
    activityId: dto.activity_id,
    direction: dto.direction,
    passNumber: dto.pass_number,
    elapsedSeconds: dto.elapsed_seconds,
    avgSpeedMps: dto.avg_speed_mps ?? null,
    activityType: dto.activity_type ?? null,
    avgPowerW: dto.avg_power_w ?? null,
    avgHrBpm: dto.avg_hr_bpm ?? null,
    rssPerMin: dto.rss_per_min ?? null,
    timeStart: dto.time_start,
    timeEnd: dto.time_end,
  }
}

/**
 * Fetches one page of the segment library. The library is global: every stored
 * segment is listed for every user, which is why the route needs only a read
 * scope and no owner filter.
 *
 * @param params - Page number and size.
 * @param signal - Optional abort signal for TanStack Query cancellation.
 * @returns The page's segments plus the total record count.
 * @throws {HttpError} When the request fails.
 */
export async function fetchSegments(
  { page, numRecords }: { page: number; numRecords: number },
  signal?: AbortSignal,
): Promise<SegmentsPage> {
  const params = new URLSearchParams({
    page_number: String(page),
    num_records: String(numRecords),
  })
  const response = await apiFetch<Schemas['Page_SegmentListItemRead_']>(
    `/segments?${params.toString()}`,
    {
      signal,
    },
  )
  return {
    items: (response.items ?? []).map(mapSegmentSummary),
    total: response.total ?? 0,
  }
}

/**
 * Fetches one segment definition with its geometry.
 *
 * @param id - Segment identifier.
 * @param signal - Optional abort signal.
 * @returns The segment detail.
 * @throws {HttpError} 404 when the segment does not exist.
 */
export async function fetchSegmentById(id: number, signal?: AbortSignal): Promise<SegmentDetail> {
  const dto = await apiFetch<SegmentDetailDto>(`/segments/${id}`, { signal })
  return mapSegmentDetail(dto)
}

/**
 * Fetches one page of the viewer's efforts on a segment, fastest first.
 *
 * @param id - Segment identifier.
 * @param direction - Which directions to include (the server defaults to `forward`).
 * @param params - Page number and size.
 * @param signal - Optional abort signal.
 * @returns The page of efforts plus the total record count.
 * @throws {HttpError} 404 when the segment does not exist.
 */
export async function fetchSegmentEfforts(
  id: number,
  direction: EffortDirectionFilter,
  { page, numRecords }: { page: number; numRecords: number },
  signal?: AbortSignal,
): Promise<SegmentEffortsPage> {
  const params = new URLSearchParams({
    page_number: String(page),
    num_records: String(numRecords),
    direction,
  })
  const response = await apiFetch<Schemas['Page_SegmentEffortRead_']>(
    `/segments/${id}/efforts?${params.toString()}`,
    { signal },
  )
  return {
    items: (response.items ?? []).map(mapSegmentEffort),
    total: response.total ?? 0,
  }
}

/**
 * Imports one segment definition.
 *
 * @param input - The form's segment, in the server's import contract.
 * @returns The stored segment, with its geometry.
 * @throws {HttpError} 409 when that `(source, source_id)` pair is already stored.
 */
export async function importSegment(input: SegmentImportInput): Promise<SegmentDetail> {
  const payload: SegmentImportPayload = {
    segment_id: input.segmentId,
    name: input.name,
    latlng: input.latlng,
    source: input.source,
  }
  if (input.altitude?.length) {
    payload.altitude = input.altitude
  }
  if (input.distance?.length) {
    payload.distance = input.distance
  }
  const dto = await apiFetch<SegmentDetailDto>('/segments', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return mapSegmentDetail(dto)
}

/**
 * Deletes one segment from the library. The server replies `204 No Content`, so
 * there is no response body to map; the segment's stored efforts cascade with it.
 *
 * @param id - Segment identifier.
 * @throws {HttpError} 404 when the segment does not exist.
 */
export async function deleteSegment(id: number): Promise<void> {
  await apiFetch<void>(`/segments/${id}`, { method: 'DELETE', responseType: 'void' })
}

/**
 * Queues a full re-match of every activity against the segment library. The
 * server runs it off the request path, so this only reports that it was queued.
 *
 * @returns Nothing when the rescan was accepted.
 * @throws {HttpError} When the request fails.
 */
export async function queueSegmentsBackfill(): Promise<void> {
  await apiFetch<{ queued: boolean }>('/segments/backfill', {
    method: 'POST',
    body: JSON.stringify({}),
  })
}
