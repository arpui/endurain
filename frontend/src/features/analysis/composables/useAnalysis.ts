/**
 * TanStack Query composables for the analysis area. Both queries are gated on
 * authentication; the settings write and the two queued passes invalidate the
 * analysis domain so the series charts refetch against the new numbers.
 */

import { computed, type MaybeRefOrGetter, toValue } from 'vue'
import { storeToRefs } from 'pinia'
import { keepPreviousData, useQuery } from '@tanstack/vue-query'

import type { AnalysisSettingsUpdateDto, FitnessQuery } from '../types'

import { queryKeys } from '@/services/queryKeys'
import { useInvalidatingMutation } from '@/composables/useInvalidatingMutation'
import { useAuthStore } from '@/features/auth/stores/auth'
import {
  fetchAnalysisSettings,
  fetchFitnessSeries,
  queueLoadRecompute,
  queueThresholdRefresh,
  updateAnalysisSettings,
} from '@/features/analysis/services/analysis'

/**
 * The caller's analysis parameters, defaults included.
 *
 * @returns The TanStack query result for the settings.
 */
export function useAnalysisSettingsQuery() {
  const { isAuthenticated } = storeToRefs(useAuthStore())

  return useQuery({
    queryKey: queryKeys.analysis.settings(),
    queryFn: ({ signal }) => fetchAnalysisSettings(signal),
    enabled: isAuthenticated,
  })
}

/**
 * The daily load series with its fatigue/fitness/form curves. Keyed on the
 * query parameters so switching method, window or τ does not overwrite another
 * combination in the cache, and `keepPreviousData` keeps the chart stable while
 * the next window loads.
 *
 * @param query - Reactive query parameters.
 * @returns The TanStack query result for the series.
 */
export function useFitnessSeriesQuery(query: MaybeRefOrGetter<FitnessQuery>) {
  const { isAuthenticated } = storeToRefs(useAuthStore())

  return useQuery({
    queryKey: computed(() => queryKeys.analysis.fitness({ ...toValue(query) })),
    queryFn: ({ signal }) => fetchFitnessSeries(toValue(query), signal),
    placeholderData: keepPreviousData,
    enabled: isAuthenticated,
  })
}

/**
 * Writes analysis parameters and cascades a refetch of the whole domain: the
 * series shown on screen were computed from the old time constants.
 *
 * @returns The mutation to call with a partial settings body.
 */
export function useUpdateAnalysisSettingsMutation() {
  return useInvalidatingMutation({
    mutationKey: ['analysis', 'settings'],
    mutationFn: (payload: AnalysisSettingsUpdateDto) => updateAnalysisSettings(payload),
    invalidateKey: queryKeys.analysis.all(),
  })
}

/**
 * Queues re-deriving thresholds from the athlete's own records. The work runs
 * on the server's scheduler; the invalidation only refreshes what the UI shows.
 *
 * @returns The mutation to call when the button is pressed.
 */
export function useQueueThresholdRefreshMutation() {
  return useInvalidatingMutation({
    mutationKey: ['analysis', 'threshold-refresh'],
    mutationFn: () => queueThresholdRefresh(),
    invalidateKey: queryKeys.analysis.all(),
  })
}

/**
 * Queues re-measuring every activity with the current thresholds and formulas.
 *
 * @returns The mutation to call when the button is pressed.
 */
export function useQueueRecomputeMutation() {
  return useInvalidatingMutation({
    mutationKey: ['analysis', 'recompute'],
    mutationFn: () => queueLoadRecompute(),
    invalidateKey: queryKeys.analysis.all(),
  })
}
