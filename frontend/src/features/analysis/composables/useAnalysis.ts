/**
 * TanStack Query composables for the analysis area. Both queries are gated on
 * authentication; the settings write and the two queued passes invalidate the
 * analysis domain so the series charts refetch against the new numbers.
 */

import { computed, type MaybeRefOrGetter, toValue } from 'vue'
import { storeToRefs } from 'pinia'
import { keepPreviousData, useQuery } from '@tanstack/vue-query'

import type { AnalysisSettingsUpdateDto, FitnessQuery, TrailVerdict } from '../types'
import type { GeoPoint } from '@/composables/useMapProvider'

import { queryKeys } from '@/services/queryKeys'
import { useInvalidatingMutation } from '@/composables/useInvalidatingMutation'
import { useAuthStore } from '@/features/auth/stores/auth'
import {
  fetchActivityIntervals,
  deleteTrailSectionMark,
  fetchRoutes,
  fetchTrailNetwork,
  createRoute,
  deleteRoute,
  fetchTrailSections,
  fetchTrailVariants,
  updateRoute,
  postVariantMark,
  postTrailSectionMark,
  fetchActivityLapDynamics,
  fetchActivityLoadsSummaries,
  fetchAnalysisSettings,
  fetchBestTimes,
  fetchCpTrend,
  fetchPowerFlags,
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
 * The per-session critical-power evolution for one sport and history window.
 *
 * @param sport - Reactive sport group to read.
 * @param windowDays - Reactive days of history, `null` for all of it.
 * @returns The TanStack query result for the trend.
 */
/**
 * Query for the sessions the duration-intensity curve held out as implausible.
 *
 * @param sport - Reactive sport group to read.
 * @returns The TanStack query result for the flags.
 */
export function usePowerFlagsQuery(sport: MaybeRefOrGetter<string>) {
  const { isAuthenticated } = storeToRefs(useAuthStore())

  return useQuery({
    queryKey: computed(() => queryKeys.analysis.powerFlags(toValue(sport))),
    queryFn: ({ signal }) => fetchPowerFlags(toValue(sport), signal),
    enabled: computed(() => isAuthenticated.value && toValue(sport) !== ''),
  })
}

export function useCpTrendQuery(
  sport: MaybeRefOrGetter<string>,
  windowDays: MaybeRefOrGetter<number | null>,
) {
  const { isAuthenticated } = storeToRefs(useAuthStore())

  return useQuery({
    queryKey: computed(() => queryKeys.analysis.cpTrend(toValue(sport), toValue(windowDays))),
    queryFn: ({ signal }) => fetchCpTrend(toValue(sport), toValue(windowDays), signal),
    placeholderData: keepPreviousData,
    enabled: computed(() => isAuthenticated.value && toValue(sport) !== ''),
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

/**
 * Which stretches of one trail run read as technical terrain.
 *
 * Computed on read by the server from the activity's own speed, altitude and
 * cadence streams; only run sports answer, so the parent gates the query on
 * the activity type. Experimental: the calibration loop with the athlete is open.
 *
 * @param activityId - Reactive activity id.
 * @param enabled - Whether the parent wants it (loaded, owned, trail run).
 * @returns The TanStack query result for the technical sections.
 */
export function useTrailSectionsQuery(
  activityId: MaybeRefOrGetter<number | null | undefined>,
  enabled: MaybeRefOrGetter<boolean> = true,
) {
  const { isAuthenticated } = storeToRefs(useAuthStore())
  const id = computed(() => {
    const value = toValue(activityId)
    return typeof value === 'number' && value > 0 ? value : 0
  })

  return useQuery({
    queryKey: computed(() => queryKeys.analysis.trailSections(id.value)),
    queryFn: ({ signal }) => fetchTrailSections(id.value, signal),
    enabled: computed(() => id.value > 0 && toValue(enabled) && isAuthenticated.value),
  })
}

/**
 * The stored loads and power provenance of a set of activities, fetched in one
 * call: the activities list asks for the page it shows, the detail strip for
 * its own id. Rows are the athlete's own; ids of others read as absent.
 *
 * @param activityIds - Reactive list of activity ids to summarize.
 * @returns The TanStack query result for a map of activity id to its summary.
 */
export function useActivityLoadsSummariesQuery(activityIds: MaybeRefOrGetter<number[]>) {
  const { isAuthenticated } = storeToRefs(useAuthStore())
  const ids = computed(() => toValue(activityIds))

  return useQuery({
    queryKey: computed(() => queryKeys.analysis.loads(ids.value.join(','))),
    queryFn: ({ signal }) => fetchActivityLoadsSummaries(ids.value, signal),
    enabled: computed(() => ids.value.length > 0 && isAuthenticated.value),
  })
}

/**
 * The athlete's best splits over the canonical race distances.
 *
 * Computed on read from the activities' own samples, so the window can move
 * without any stored table behind it.
 *
 * @param months - Reactive window in months back from today; ``null`` for all history.
 * @returns The TanStack query result for the best-splits table.
 */
export function useBestTimesQuery(months: MaybeRefOrGetter<number | null>) {
  const { isAuthenticated } = storeToRefs(useAuthStore())

  return useQuery({
    queryKey: computed(() => queryKeys.analysis.bestTimes(toValue(months))),
    queryFn: ({ signal }) => fetchBestTimes(toValue(months), signal),
    enabled: computed(() => isAuthenticated.value),
  })
}

/**
 * Per-lap dynamics of one activity: power, stride length, elevation split and aRSS.
 *
 * The owner's view of the laps table: the aRSS column is referenced to their own
 * threshold, so the query is not issued for other viewers.
 *
 * @param activityId - Reactive activity id.
 * @param enabled - Whether the parent activity has loaded and the viewer owns it.
 * @returns The TanStack query result for the dynamics rows.
 */
export function useLapDynamicsQuery(
  activityId: MaybeRefOrGetter<number | null | undefined>,
  enabled: MaybeRefOrGetter<boolean> = true,
) {
  const { isAuthenticated } = storeToRefs(useAuthStore())
  const id = computed(() => {
    const value = toValue(activityId)
    return typeof value === 'number' && value > 0 ? value : 0
  })

  return useQuery({
    queryKey: computed(() => queryKeys.analysis.lapDynamics(id.value)),
    queryFn: ({ signal }) => fetchActivityLapDynamics(id.value, signal),
    enabled: computed(() => id.value > 0 && toValue(enabled) && isAuthenticated.value),
  })
}

/**
 * Place or take back a verdict on one measured stretch.
 *
 * The verdict is the calibration input: it is what a future weight or threshold
 * change is judged against, so it has to be one tap and undoable or nobody places
 * them. It is not guesswork the client can predict, so it follows the write-then-
 * invalidate pattern; the sections query carries the mark back into the table.
 *
 * @param activityId - Reactive activity id.
 * @returns The TanStack mutation, resolving once the verdict is stored.
 */
export function useTrailSectionMarkMutation(
  activityId: MaybeRefOrGetter<number | null | undefined>,
) {
  const id = computed(() => {
    const value = toValue(activityId)
    return typeof value === 'number' && value > 0 ? value : 0
  })

  return useInvalidatingMutation({
    mutationKey: ['analysis', 'trail-section-mark'],
    invalidateKey: ['analysis', 'trail-sections'],
    mutationFn: ({ startM, verdict }: { startM: number; verdict: TrailVerdict | null }) =>
      verdict === null
        ? deleteTrailSectionMark(id.value, startM)
        : postTrailSectionMark(id.value, startM, verdict),
  })
}

/**
 * The athlete's crossed ways as a network: each judged by its descents, with
 * the spread across crossings shown rather than hidden.
 *
 * @returns The TanStack query for the network payload.
 */
/**
 * @param limit - Ways to serve; defaults high so the whole crossed network arrives.
 * @returns The TanStack query for the network payload.
 */
export function useTrailNetworkQuery(limit: MaybeRefOrGetter<number> = 5000) {
  const { isAuthenticated } = storeToRefs(useAuthStore())
  const value = computed(() => toValue(limit))
  return useQuery({
    queryKey: queryKeys.analysis.trailNetwork(value.value),
    queryFn: ({ signal }) => fetchTrailNetwork(value.value, signal),
    enabled: isAuthenticated,
  })
}

/**
 * Reconnection variants suggested along one recorded route (experimental).
 *
 * @param activityId - Reactive activity id whose track is the plan.
 * @returns The TanStack query for the variant analysis.
 */
export function useTrailVariantsQuery(
  activityId: MaybeRefOrGetter<number | null | undefined>,
  enabled: MaybeRefOrGetter<boolean> = true,
) {
  const { isAuthenticated } = storeToRefs(useAuthStore())
  const id = computed(() => {
    const value = toValue(activityId)
    return typeof value === 'number' && value > 0 ? value : 0
  })

  return useQuery({
    queryKey: computed(() => queryKeys.analysis.trailVariants(id.value)),
    queryFn: ({ signal }) => fetchTrailVariants(id.value, signal),
    enabled: computed(() => id.value > 0 && toValue(enabled) && isAuthenticated.value),
  })
}

/**
 * Judge one suggested variant: real ground or a suggestion to bury.
 *
 * The verdict is durable: a ``falsa`` never resurfaces as a suggestion, which is
 * how the engine learns the difference between ground and GPS artifacts.
 *
 * @param activityId - Reactive activity id of the planned route.
 * @returns The mutation; ``verdict: null`` takes the judgement back.
 */
export function useVariantMarkMutation(activityId: MaybeRefOrGetter<number | null | undefined>) {
  const id = computed(() => {
    const value = toValue(activityId)
    return typeof value === 'number' && value > 0 ? value : 0
  })
  return useInvalidatingMutation({
    mutationKey: ['analysis', 'variant-mark'],
    invalidateKey: ['analysis', 'trail-variants'],
    mutationFn: ({ splitM, verdict }: { splitM: number; verdict: 'real' | 'falsa' | null }) =>
      postVariantMark(id.value, splitM, verdict),
  })
}

/**
 * The athlete's saved designed routes.
 *
 * @returns The TanStack query for the route list.
 */
export function useRoutesQuery() {
  const { isAuthenticated } = storeToRefs(useAuthStore())
  return useQuery({
    queryKey: queryKeys.analysis.routes(),
    queryFn: ({ signal }) => fetchRoutes(signal),
    enabled: isAuthenticated,
  })
}

/**
 * Save a route drawn over the network; totals are measured by the server.
 *
 * @returns The mutation to call with name, polyline and optional notes.
 */
export function useCreateRouteMutation() {
  return useInvalidatingMutation({
    mutationKey: ['analysis', 'route-create'],
    invalidateKey: queryKeys.analysis.all(),
    mutationFn: ({ name, points, notes }: { name: string; points: GeoPoint[]; notes?: string }) =>
      createRoute(name, points, notes),
  })
}

/**
 * Edit a saved route; the server measures the new totals again.
 *
 * @returns The mutation to call with id, name, polyline and optional notes.
 */
export function useUpdateRouteMutation() {
  return useInvalidatingMutation({
    mutationKey: ['analysis', 'route-update'],
    invalidateKey: queryKeys.analysis.all(),
    mutationFn: ({
      routeId,
      name,
      points,
      notes,
    }: {
      routeId: number
      name: string
      points: GeoPoint[]
      notes?: string
    }) => updateRoute(routeId, name, points, notes),
  })
}

/**
 * Delete one saved route.
 *
 * @returns The mutation to call with the route id.
 */
export function useDeleteRouteMutation() {
  return useInvalidatingMutation({
    mutationKey: ['analysis', 'route-delete'],
    invalidateKey: queryKeys.analysis.all(),
    mutationFn: (routeId: number) => deleteRoute(routeId),
  })
}
