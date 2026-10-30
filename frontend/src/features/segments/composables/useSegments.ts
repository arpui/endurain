import { computed, type MaybeRefOrGetter, toValue } from 'vue'
import { storeToRefs } from 'pinia'
import { keepPreviousData, useQuery } from '@tanstack/vue-query'

import type { EffortDirectionFilter } from '../types'

import { queryKeys } from '@/services/queryKeys'
import { useInvalidatingMutation } from '@/composables/useInvalidatingMutation'
import { useAuthStore } from '@/features/auth/stores/auth'
import {
  deleteSegment,
  fetchSegmentById,
  fetchSegmentEfforts,
  fetchSegments,
  importSegment,
  queueSegmentsBackfill,
} from '@/features/segments/services/segments'

/** Resolves a reactive id to a positive number, or `0` when invalid. */
function resolveId(id: MaybeRefOrGetter<number | null | undefined>): number {
  const value = toValue(id)
  return typeof value === 'number' && value > 0 ? value : 0
}

/**
 * One page of the segment library. Numbered pagination so the page size honours
 * the server's `num_records_per_page` setting, with `keepPreviousData` so paging
 * never flashes an empty list. The library is global, so the only gate is
 * authentication (every user reads the same definitions).
 *
 * @param page - Reactive 1-based page number.
 * @param pageSize - Reactive records-per-page.
 * @returns The TanStack query result for the current library page.
 */
export function useSegmentsQuery(
  page: MaybeRefOrGetter<number>,
  pageSize: MaybeRefOrGetter<number>,
) {
  const { isAuthenticated } = storeToRefs(useAuthStore())

  return useQuery({
    queryKey: computed(() =>
      queryKeys.segments.list({ page: toValue(page), numRecords: toValue(pageSize) }),
    ),
    queryFn: ({ signal }) =>
      fetchSegments({ page: toValue(page), numRecords: toValue(pageSize) }, signal),
    placeholderData: keepPreviousData,
    enabled: isAuthenticated,
  })
}

/**
 * One segment definition (geometry and altitudes), for the detail page.
 *
 * @param id - Reactive segment id.
 * @returns The TanStack query result for the segment.
 */
export function useSegmentDetailQuery(id: MaybeRefOrGetter<number | null | undefined>) {
  const { isAuthenticated } = storeToRefs(useAuthStore())

  return useQuery({
    queryKey: computed(() => queryKeys.segments.detail(resolveId(id))),
    queryFn: ({ signal }) => fetchSegmentById(resolveId(id), signal),
    enabled: computed(() => resolveId(id) > 0 && isAuthenticated.value),
    staleTime: 5 * 60_000,
  })
}

/**
 * One page of the viewer's efforts on a segment, fastest first. Keyed on the
 * direction filter so switching to include reverse passes does not overwrite the
 * forward page in the cache.
 *
 * @param id - Reactive segment id.
 * @param direction - Reactive direction filter (`forward` | `reverse` | `all`).
 * @param page - Reactive 1-based page number.
 * @param pageSize - Reactive records-per-page.
 * @returns The TanStack query result for the effort page.
 */
export function useSegmentEffortsQuery(
  id: MaybeRefOrGetter<number | null | undefined>,
  direction: MaybeRefOrGetter<EffortDirectionFilter>,
  page: MaybeRefOrGetter<number>,
  pageSize: MaybeRefOrGetter<number>,
) {
  const { isAuthenticated } = storeToRefs(useAuthStore())

  return useQuery({
    queryKey: computed(() => queryKeys.segments.efforts(resolveId(id), toValue(direction))),
    queryFn: ({ signal }) =>
      fetchSegmentEfforts(
        resolveId(id),
        toValue(direction),
        { page: toValue(page), numRecords: toValue(pageSize) },
        signal,
      ),
    placeholderData: keepPreviousData,
    enabled: computed(() => resolveId(id) > 0 && isAuthenticated.value),
  })
}

/**
 * Imports a segment definition. Invalidates the whole segments domain so the
 * library list shows the new row, then the caller queues a rescan to match the
 * activities that already exist.
 *
 * @returns The mutation performing the import.
 */
export function useImportSegmentMutation() {
  return useInvalidatingMutation({
    mutationKey: ['segments', 'import'],
    mutationFn: importSegment,
    invalidateKey: queryKeys.segments.all(),
  })
}

/**
 * Deletes one segment. Invalidates the whole segments domain so the library
 * drops the row and any effort page reading it refetches.
 *
 * @returns The mutation performing the delete.
 */
export function useDeleteSegmentMutation() {
  return useInvalidatingMutation<void, number>({
    mutationKey: ['segments', 'delete'],
    mutationFn: deleteSegment,
    invalidateKey: queryKeys.segments.all(),
  })
}

/**
 * Queues the full re-match of every activity against the library. The work runs
 * off the request path, so this only records that it was accepted; invalidating
 * the domain makes a later effort refetch see the fresh rows.
 *
 * @returns The mutation queueing the rescan.
 */
export function useSegmentsBackfillMutation() {
  return useInvalidatingMutation({
    mutationKey: ['segments', 'backfill'],
    mutationFn: queueSegmentsBackfill,
    invalidateKey: queryKeys.segments.all(),
  })
}
