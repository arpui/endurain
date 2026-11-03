<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import type { SegmentMatchOutcome } from '@/features/segments/types'
import type { Schemas } from '@/types'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { RouterLink } from 'vue-router'

import { useSegmentMatchDebugQuery } from '@/features/segments/composables/useSegments'
import { formatSegmentDistance } from '@/features/segments/utils/format'

/**
 * Match-debug dialog for one activity: a verdict row per segment whose
 * bounding box reaches the activity — matched ones first, then the near
 * misses with the span and coverage the best attempt reached. This is what
 * answers "why does this activity show no effort on that segment".
 */
const open = defineModel<boolean>('open', { required: true })
const props = defineProps<{
  activityId: number
  units: Schemas['Units']
}>()

const { t } = useI18n()
const { data, isPending, isError, refetch } = useSegmentMatchDebugQuery(
  computed(() => props.activityId),
  open,
)

/** Outcomes that mean the segment scored the activity. */
function isMatched(outcome: SegmentMatchOutcome): boolean {
  return outcome === 'matched'
}

/** Formats a 0..1 fraction as a whole-percent label. */
function pct(value: number): string {
  return `${Math.round(value * 100)}%`
}
</script>

<template>
  <Dialog v-model:open="open">
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{{ t('activities.segments.debug.title') }}</DialogTitle>
        <DialogDescription>{{ t('activities.segments.debug.description') }}</DialogDescription>
      </DialogHeader>

      <p v-if="isPending" class="text-hint text-muted-foreground">{{ t('segments.loading') }}</p>
      <p v-else-if="isError" class="text-hint text-destructive">
        {{ t('activities.segments.debug.failed') }}
        <Button variant="outline" size="sm" class="ms-2" @click="refetch()">
          {{ t('search.error.retry') }}
        </Button>
      </p>
      <p v-else-if="!data || data.length === 0" class="text-hint text-muted-foreground">
        {{ t('activities.segments.debug.none') }}
      </p>

      <div v-else class="overflow-x-auto">
        <table class="w-full min-w-[34rem] border-collapse text-meta">
          <thead>
            <tr class="text-caption border-b border-border text-left">
              <th class="py-2 pe-3 font-medium">{{ t('activities.segments.segment') }}</th>
              <th class="py-2 pe-3 font-medium">{{ t('activities.segments.debug.outcome') }}</th>
              <th class="py-2 pe-3 font-medium">{{ t('activities.segments.debug.candidates') }}</th>
              <th class="py-2 pe-3 font-medium">{{ t('activities.segments.debug.bestSpan') }}</th>
              <th class="py-2 font-medium">{{ t('activities.segments.debug.coverage') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in data"
              :key="item.segmentId"
              class="border-b border-border/60 last:border-0"
            >
              <td class="py-2 pe-3">
                <RouterLink
                  class="font-medium text-foreground underline-offset-4 hover:underline"
                  :to="{ name: 'segment-detail', params: { id: item.segmentId } }"
                >
                  {{ item.name }}
                </RouterLink>
                <span class="text-caption ms-2 text-muted-foreground">
                  {{ formatSegmentDistance(item.distanceM, units) }}
                </span>
              </td>
              <td class="py-2 pe-3">
                <Badge :variant="isMatched(item.outcome) ? 'default' : 'secondary'">
                  {{ t(`activities.segments.debug.outcome_${item.outcome}`) }}
                </Badge>
                <span
                  v-if="!isMatched(item.outcome) && item.rejectedPassCount > 0"
                  class="text-caption ms-2 text-muted-foreground"
                >
                  {{
                    t('activities.segments.debug.passesRejected', { count: item.rejectedPassCount })
                  }}
                </span>
              </td>
              <td class="py-2 pe-3 text-muted-foreground">{{ item.candidateCount }}</td>
              <td class="py-2 pe-3 text-muted-foreground">{{ pct(item.bestSpanFrac) }}</td>
              <td class="py-2 text-muted-foreground">{{ pct(item.bestCoverage) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </DialogContent>
  </Dialog>
</template>
