<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RefreshCw } from '@lucide/vue'
import { useRoute } from 'vue-router'

import type { EffortDirectionFilter, SegmentEffort } from '@/features/segments/types'
import type { GeoPoint } from '@/composables/useMapProvider'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { Pagination } from '@/components/ui/pagination'
import RouteMap from '@/components/RouteMap.vue'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { useCurrentUser } from '@/features/auth/composables/useCurrentUser'
import { useRecordsPerPage } from '@/features/config/composables/useRecordsPerPage'
import { useListPagination } from '@/composables/useListPagination'
import { useToasts } from '@/composables/useToasts'
import {
  useSegmentDetailQuery,
  useSegmentEffortsQuery,
  useSegmentsBackfillMutation,
} from '@/features/segments/composables/useSegments'
import SegmentEffortsTable from '@/features/segments/components/SegmentEffortsTable.vue'
import SegmentElevationProfile from '@/features/segments/components/SegmentElevationProfile.vue'
import { formatSegmentDistance, formatSegmentElevation } from '@/features/segments/utils/format'

const { t } = useI18n()
const toasts = useToasts()
const route = useRoute()
const { data: currentUser } = useCurrentUser()

const units = computed(() => currentUser.value?.units ?? 'metric')

const segmentId = computed(() => Number(route.params.id))
const detailQuery = useSegmentDetailQuery(segmentId)
const segment = computed(() => detailQuery.data.value ?? null)

/**
 * Reverse passes are stored but not rankable, so the leaderboard hides them
 * unless the reader asks: switching to `all` mixes them into the same fastest-first
 * page, where each row is labelled.
 */
const includeReverse = ref(false)
const direction = computed<EffortDirectionFilter>(() => (includeReverse.value ? 'all' : 'forward'))

const page = ref(1)
const { recordsPerPage } = useRecordsPerPage()
const effortsQuery = useSegmentEffortsQuery(segmentId, direction, page, recordsPerPage)

const efforts = computed<SegmentEffort[]>(() => effortsQuery.data.value?.items ?? [])
const totalCount = computed(() => effortsQuery.data.value?.total ?? 0)
const { totalPages, reset: resetPage } = useListPagination(page, totalCount, recordsPerPage)

// Changing the direction changes the result set, so restart at page 1.
watch(direction, resetPage)

/** The polyline the map draws, in the definition's canonical order. */
const track = computed<GeoPoint[]>(() =>
  (segment.value?.geometry ?? []).map((point) => ({ lat: point[0] ?? 0, lng: point[1] ?? 0 })),
)

const metaLine = computed(() => {
  const current = segment.value
  if (!current) {
    return ''
  }
  const parts = [formatSegmentDistance(current.distanceM, units.value)]
  if (current.elevationGainM !== null) {
    parts.push(formatSegmentElevation(current.elevationGainM, units.value))
  }
  return parts.join(' · ')
})

const backfillMutation = useSegmentsBackfillMutation()

/** Queues a re-match; useful when the effort list is empty for an imported segment. */
function queueRescan(): void {
  backfillMutation.mutate(undefined, {
    onSuccess: () => toasts.success(t('segments.rescan.queued')),
    onError: () => toasts.error(t('segments.rescan.failed')),
  })
}
</script>

<template>
  <section class="flex flex-col gap-3">
    <ErrorState
      v-if="detailQuery.isError.value"
      :title="t('segments.error.title')"
      :description="t('segments.error.description')"
      @retry="detailQuery.refetch()"
    />

    <template v-else-if="segment">
      <header class="flex flex-wrap items-start justify-between gap-2">
        <div class="flex min-w-0 flex-col gap-1">
          <h1 class="text-page-title">{{ segment.name }}</h1>
          <p class="text-body text-muted-foreground">
            {{ metaLine }} ·
            {{ t(segment.source === 'manual' ? 'segments.sourceManual' : 'segments.sourceStrava') }}
          </p>
        </div>
        <Button variant="ghost" :disabled="backfillMutation.isPending.value" @click="queueRescan">
          <RefreshCw class="size-4" aria-hidden="true" />
          {{ t('segments.rescan.button') }}
        </Button>
      </header>

      <Card class="flex min-w-0 flex-col gap-2">
        <div class="h-80 w-full min-w-0">
          <RouteMap :points="track" :label="t('segments.map.label')" />
        </div>
      </Card>

      <SegmentElevationProfile :segment="segment" :units="units" />

      <Card class="flex flex-col gap-3">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h2 class="text-card-heading">{{ t('segments.efforts.title') }}</h2>
          <label class="flex items-center gap-2 text-caption text-muted-foreground">
            <Switch v-model="includeReverse" />
            {{ t('segments.efforts.includeReverse') }}
          </label>
        </div>

        <p
          v-if="effortsQuery.isPending.value"
          role="status"
          class="text-caption text-muted-foreground"
        >
          {{ t('segments.loading') }}
        </p>

        <EmptyState
          v-else-if="efforts.length === 0"
          :title="t('segments.efforts.empty.title')"
          :description="t('segments.efforts.empty.description')"
        />

        <template v-else>
          <SegmentEffortsTable :efforts="efforts" :units="units" />
          <Pagination
            v-if="totalPages > 1"
            :page="page"
            :total-pages="totalPages"
            @update:page="page = $event"
          />
        </template>
      </Card>
    </template>

    <div v-else class="flex flex-col gap-3" role="status" :aria-label="t('segments.loading')">
      <Skeleton class="h-8 w-1/2" />
      <Skeleton class="h-80 w-full" />
    </div>
  </section>
</template>
