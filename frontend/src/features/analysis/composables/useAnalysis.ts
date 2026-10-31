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
  fetchActivityIntervals,
  fetchAnalysisSettings,
  fetchFitnessSeries,
  fetchPowerDuration,
  fetchWprimeBalance,
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

/**
 * The duration-intensity curve of one sport, with the fit recomputed for the
 * requested preview window. Keyed on sport + window so previews of different
 * ranges sit side by side in the cache.
 *
 * @param sport - Reactive sport group.
 * @param window - Reactive preview fit window (`null` ends keep stored defaults).
 * @returns The TanStack query result for the curve.
 */
export function usePowerDurationQuery(
  sport: MaybeRefOrGetter<string>,
  window: MaybeRefOrGetter<{ fitFromS: number | null; fitToS: number | null }>,
) {
  const { isAuthenticated } = storeToRefs(useAuthStore())

  return useQuery({
    queryKey: computed(() => {
      const { fitFromS, fitToS } = toValue(window)
      return queryKeys.analysis.powerDuration(toValue(sport), fitFromS, fitToS)
    }),
    queryFn: ({ signal }) => fetchPowerDuration(toValue(sport), toValue(window), signal),
    placeholderData: keepPreviousData,
    enabled: computed(() => toValue(sport) !== '' && isAuthenticated.value),
  })
}

/**
 * One activity's W' balance, computed on read by the server.
 *
 * @param activityId - Reactive activity id.
 * @returns The TanStack query result for the balance series.
 */
export function useWprimeBalanceQuery(activityId: MaybeRefOrGetter<number | null | undefined>) {
  const { isAuthenticated } = storeToRefs(useAuthStore())
  const id = computed(() => {
    const value = toValue(activityId)
    return typeof value === 'number' && value > 0 ? value : 0
  })

  return useQuery({
    queryKey: computed(() => queryKeys.analysis.wprime(id.value)),
    queryFn: ({ signal }) => fetchWprimeBalance(id.value, signal),
    enabled: computed(() => id.value > 0 && isAuthenticated.value),
  })
}

/**
 * How one activity breaks down into efforts: reps, climbs, the steady parts.
 *
 * Computed on read by the server from the activity's own samples against that
 * athlete's own numbers, so it needs no stored rows and follows a threshold change
 * at once. Only the activity's owner can read it, hence the authentication gate.
 *
 * @param activityId - Reactive activity id.
 * @param enabled - Whether the parent activity has loaded.
 * @returns The TanStack query result for the breakdown.
 */
export function useActivityIntervalsQuery(
  activityId: MaybeRefOrGetter<number | null | undefined>,
  enabled: MaybeRefOrGetter<boolean> = true,
) {
  const { isAuthenticated } = storeToRefs(useAuthStore())
  const id = computed(() => {
    const value = toValue(activityId)
    return typeof value === 'number' && value > 0 ? value : 0
  })

  return useQuery({
    queryKey: computed(() => queryKeys.analysis.intervals(id.value)),
    queryFn: ({ signal }) => fetchActivityIntervals(id.value, signal),
    enabled: computed(() => id.value > 0 && toValue(enabled) && isAuthenticated.value),
  })
}
