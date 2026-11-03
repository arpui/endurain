<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Route } from '@lucide/vue'
import { RouterLink } from 'vue-router'

import type { SegmentSummary } from '@/features/segments/types'

import { useCurrentUser } from '@/features/auth/composables/useCurrentUser'
import { formatSegmentDistance, formatSegmentElevation } from '@/features/segments/utils/format'

/**
 * One segment row in the search results: route icon, name, and the
 * length + gain caption the library list shows, plus the reader's effort
 * count, linking to the segment detail view.
 */
const props = defineProps<{ segment: SegmentSummary }>()

const { t } = useI18n()
const { data: currentUser } = useCurrentUser()
const units = computed(() => currentUser.value?.units ?? 'metric')

/** Formats the length + gain caption, identically to the library row. */
const meta = computed(() => {
  const parts = [formatSegmentDistance(props.segment.distanceM, units.value)]
  if (props.segment.elevationGainM !== null) {
    parts.push(formatSegmentElevation(props.segment.elevationGainM, units.value))
  }
  return parts.join(' · ')
})
</script>

<template>
  <RouterLink
    :to="{ name: 'segment-detail', params: { id: segment.id } }"
    class="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-muted/50"
  >
    <span
      class="flex size-10 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground"
    >
      <Route class="size-5" aria-hidden="true" />
    </span>
    <div class="min-w-0 flex-1">
      <p class="truncate font-medium text-foreground">{{ segment.name }}</p>
      <p class="truncate text-hint">{{ meta }}</p>
    </div>
    <span v-if="segment.effortCount > 0" class="shrink-0 text-hint text-muted-foreground">
      {{ t('segments.effortCount', { count: segment.effortCount }) }}
    </span>
  </RouterLink>
</template>
