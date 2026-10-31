<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { RefreshCw, Route } from '@lucide/vue'
import { RouterLink } from 'vue-router'

import type { SegmentSummary } from '@/features/segments/types'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { Pagination } from '@/components/ui/pagination'
import { Skeleton } from '@/components/ui/skeleton'
import { useCurrentUser } from '@/features/auth/composables/useCurrentUser'
import { useRecordsPerPage } from '@/features/config/composables/useRecordsPerPage'
import { useListPagination } from '@/composables/useListPagination'
import { useToasts } from '@/composables/useToasts'
import {
  useSegmentsBackfillMutation,
  useSegmentsQuery,
} from '@/features/segments/composables/useSegments'
import { formatSegmentDistance, formatSegmentElevation } from '@/features/segments/utils/format'
import SegmentImportDialog from '@/features/segments/components/SegmentImportDialog.vue'

const { t } = useI18n()
const toasts = useToasts()
const { data: currentUser } = useCurrentUser()

const units = computed(() => currentUser.value?.units ?? 'metric')

const page = ref(1)
const { recordsPerPage } = useRecordsPerPage()
const listQuery = useSegmentsQuery(page, recordsPerPage)

const segments = computed<SegmentSummary[]>(() => listQuery.data.value?.items ?? [])
const totalCount = computed(() => listQuery.data.value?.total ?? 0)
const { totalPages } = useListPagination(page, totalCount, recordsPerPage)

const isPending = computed(() => listQuery.isPending.value)
const isError = computed(() => listQuery.isError.value)
const isEmpty = computed(() => !isPending.value && !isError.value && segments.value.length === 0)

const isImportOpen = ref(false)
const backfillMutation = useSegmentsBackfillMutation()

/** Queues the full re-match; the work itself runs on the server's scheduler. */
function queueRescan(): void {
  backfillMutation.mutate(undefined, {
    onSuccess: () => toasts.success(t('segments.rescan.queued')),
    onError: () => toasts.error(t('segments.rescan.failed')),
  })
}

function onImportSuccess(message: string): void {
  toasts.success(message)
}

function onImportError(message: string): void {
  toasts.error(message)
}

/** Formats the length + gain caption of one library row. */
function metaOf(segment: SegmentSummary): string {
  const parts = [formatSegmentDistance(segment.distanceM, units.value)]
  if (segment.elevationGainM !== null) {
    parts.push(formatSegmentElevation(segment.elevationGainM, units.value))
  }
  return parts.join(' · ')
}

/** i18n key for where a definition came from. */
function sourceLabelKey(source: string): string {
  return source === 'manual' ? 'segments.sourceManual' : 'segments.sourceStrava'
}
</script>

<template>
  <section class="flex flex-col gap-3">
    <header class="flex flex-wrap items-center justify-between gap-2">
      <div class="flex flex-col gap-1">
        <h1 class="text-page-title">{{ t('segments.title') }}</h1>
        <p class="text-body">{{ t('segments.subtitle') }}</p>
      </div>
      <div class="flex flex-wrap gap-2">
        <Button variant="ghost" :disabled="backfillMutation.isPending.value" @click="queueRescan">
          <RefreshCw class="size-4" aria-hidden="true" />
          {{ t('segments.rescan.button') }}
        </Button>
        <Button @click="isImportOpen = true">
          <Route class="size-4" aria-hidden="true" />
          {{ t('segments.import.button') }}
        </Button>
      </div>
    </header>

    <ErrorState
      v-if="isError"
      :title="t('segments.error.title')"
      :description="t('segments.error.description')"
      @retry="listQuery.refetch()"
    />

    <EmptyState
      v-else-if="isEmpty"
      :title="t('segments.empty.title')"
      :description="t('segments.empty.description')"
    />

    <Card v-else class="flex flex-col gap-2">
      <div
        v-if="isPending"
        class="flex flex-col gap-2"
        role="status"
        :aria-label="t('segments.loading')"
      >
        <Skeleton v-for="row in 5" :key="row" class="h-14 w-full" />
      </div>

      <ul v-else class="flex flex-col">
        <li
          v-for="segment in segments"
          :key="segment.id"
          class="border-b border-border/60 last:border-0"
        >
          <RouterLink
            class="flex items-center justify-between gap-3 py-3 transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/30"
            :to="{ name: 'segment-detail', params: { id: segment.id } }"
          >
            <span class="flex min-w-0 flex-col">
              <span class="text-item-title truncate">{{ segment.name }}</span>
              <span class="text-caption text-muted-foreground">{{ metaOf(segment) }}</span>
            </span>
            <span class="text-caption shrink-0 text-muted-foreground">
              {{ t(sourceLabelKey(segment.source)) }}
              <span v-if="segment.effortCount > 0">
                · {{ t('segments.effortCount', { count: segment.effortCount }) }}
              </span>
            </span>
          </RouterLink>
        </li>
      </ul>

      <Pagination
        v-if="totalPages > 1"
        :page="page"
        :total-pages="totalPages"
        @update:page="page = $event"
      />
    </Card>

    <SegmentImportDialog
      v-model:open="isImportOpen"
      :units="units"
      @success="onImportSuccess"
      @error="onImportError"
    />
  </section>
</template>
