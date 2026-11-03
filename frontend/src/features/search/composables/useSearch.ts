import { computed, type MaybeRefOrGetter, onBeforeUnmount, ref, toValue, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useQuery } from '@tanstack/vue-query'

import type { Activity } from '@/features/activities/types'
import type { SearchScope } from '@/features/search/types'

import { queryKeys } from '@/services/queryKeys'
import { searchActivitiesByName } from '@/features/activities/services/activities'
import { useAuthStore } from '@/features/auth/stores/auth'
import { useGearSearchQuery } from '@/features/gears/composables/useGears'
import { useSegmentSearchQuery } from '@/features/segments/composables/useSegments'
import { useUserSearchQuery } from '@/features/users/composables/useUsers'
import {
  ALL_ACTIVITY_CATEGORIES,
  ALL_GEAR_TYPES,
  filterActivitiesByCategory,
  filterGearsByType,
} from '@/features/search/utils/filters'

/** Keystroke-to-request delay, matching the gears/users search inputs. */
const SEARCH_DEBOUNCE_MS = 400

/**
 * Activity name "contains" search. Enabled only once the (debounced) term is
 * non-empty so an empty box never fires a request; gated on authentication.
 * Mirrors {@link useGearSearchQuery}/{@link useUserSearchQuery} so all three
 * scopes share one shape.
 *
 * @param term - The reactive (debounced, scope-gated) search term.
 * @returns The TanStack Query result for the matching activities.
 */
function useActivitySearchQuery(term: MaybeRefOrGetter<string>) {
  const { isAuthenticated } = storeToRefs(useAuthStore())
  const trimmed = computed(() => toValue(term).trim())

  return useQuery<Activity[]>({
    queryKey: computed(() => queryKeys.activities.search(trimmed.value)),
    queryFn: ({ signal }) => searchActivitiesByName(trimmed.value, signal),
    enabled: computed(() => isAuthenticated.value && trimmed.value.length > 0),
  })
}

/**
 * Orchestrates the global search view: the active scope, the debounced search
 * term, and the three per-scope "contains" queries. Only the active scope's
 * query ever fires — the debounced term is fed to the active scope and an empty
 * string to the others, so the inactive queries stay disabled (one request per
 * search, not three). Switching scope re-runs the current term against the new
 * scope automatically.
 *
 * @returns Reactive state and helpers for the search view.
 */
export function useSearch() {
  const scope = ref<SearchScope>('users')
  const searchTerm = ref('')
  const debouncedSearch = ref('')

  // Debounce the search box so typing doesn't fire a request per keystroke.
  let searchTimer: ReturnType<typeof setTimeout> | undefined
  watch(searchTerm, (value) => {
    if (searchTimer) {
      clearTimeout(searchTimer)
    }
    searchTimer = setTimeout(() => {
      debouncedSearch.value = value
    }, SEARCH_DEBOUNCE_MS)
  })
  onBeforeUnmount(() => {
    if (searchTimer) {
      clearTimeout(searchTimer)
    }
  })

  // Feed the debounced term only to the active scope; the others get an empty
  // string, which keeps their queries disabled (term length 0).
  const userTerm = computed(() => (scope.value === 'users' ? debouncedSearch.value : ''))
  const activityTerm = computed(() => (scope.value === 'activities' ? debouncedSearch.value : ''))
  const gearTerm = computed(() => (scope.value === 'gears' ? debouncedSearch.value : ''))
  const segmentTerm = computed(() => (scope.value === 'segments' ? debouncedSearch.value : ''))

  const userQuery = useUserSearchQuery(userTerm)
  const activityQuery = useActivitySearchQuery(activityTerm)
  const gearQuery = useGearSearchQuery(gearTerm)
  const segmentQuery = useSegmentSearchQuery(segmentTerm)

  // Sub-type filters applied client-side to the active scope's results, mirroring
  // the v1 search page. Cleared whenever the scope changes (see watch below).
  const activityCategory = ref<string>(ALL_ACTIVITY_CATEGORIES)
  const gearTypeFilter = ref<number>(ALL_GEAR_TYPES)
  watch(scope, () => {
    activityCategory.value = ALL_ACTIVITY_CATEGORIES
    gearTypeFilter.value = ALL_GEAR_TYPES
  })

  const userResults = computed(() => userQuery.data.value ?? [])
  const activityResults = computed(() =>
    filterActivitiesByCategory(activityQuery.data.value ?? [], activityCategory.value),
  )
  const gearResults = computed(() =>
    filterGearsByType(gearQuery.data.value ?? [], gearTypeFilter.value),
  )
  const segmentResults = computed(() => segmentQuery.data.value?.items ?? [])

  /** Whether a (debounced) search is currently in flight or showing results. */
  const isSearching = computed(() => debouncedSearch.value.trim().length > 0)

  // Each derived flag reads only the active scope's query so the disabled
  // queries (which report `pending`) never leak into the UI state.
  /** The query of whichever scope is active; inactive ones stay disabled. */
  function activeQuery() {
    if (scope.value === 'activities') {
      return activityQuery
    }
    if (scope.value === 'gears') {
      return gearQuery
    }
    if (scope.value === 'segments') {
      return segmentQuery
    }
    return userQuery
  }

  const isPending = computed<boolean>(() => activeQuery().isPending.value)
  const isError = computed<boolean>(() => activeQuery().isError.value)
  const resultCount = computed<number>(() => {
    if (scope.value === 'activities') {
      return activityResults.value.length
    }
    if (scope.value === 'gears') {
      return gearResults.value.length
    }
    if (scope.value === 'segments') {
      return segmentResults.value.length
    }
    return userResults.value.length
  })

  /** True only once a search has run and returned no matches. */
  const isEmpty = computed(
    () => isSearching.value && !isPending.value && !isError.value && resultCount.value === 0,
  )

  /** Clears the search field and immediately exits search mode. */
  function clearSearch(): void {
    if (searchTimer) {
      clearTimeout(searchTimer)
    }
    searchTerm.value = ''
    debouncedSearch.value = ''
  }

  /** Refetches the active scope's query (used by the error-state retry). */
  function refetchActive(): void {
    void activeQuery().refetch()
  }

  return {
    scope,
    searchTerm,
    debouncedSearch,
    activityCategory,
    gearTypeFilter,
    isSearching,
    isPending,
    isError,
    isEmpty,
    userResults,
    activityResults,
    gearResults,
    segmentResults,
    clearSearch,
    refetchActive,
  }
}
