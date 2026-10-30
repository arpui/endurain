<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import type { ActivitySegment } from '@/features/activities/types'
import type { Units } from '@/features/activities/utils/format'

import {
  combineMetric,
  formatDistance,
  formatElevation,
  formatHmsDuration,
} from '@/features/activities/utils/format'
import { formatLapTempo } from '@/features/activities/utils/metrics'

const props = defineProps<{
  segments: ActivitySegment[]
  /** Numeric activity type; decides pace-vs-speed and distance units. */
  activityType: number
  units: Units
}>()

const { t } = useI18n()

interface SegmentRow {
  key: string
  name: string
  /** Segment length and gain, e.g. `1.95 km · 154 m`. */
  length: string
  time: string
  tempo: string
  /** Pass marker, only for the second and later pass in the same direction. */
  pass: string
  isReverse: boolean
}

/**
 * One row per pass. The segment's stored geometry order decides what `reverse`
 * means, and only forward passes count as an effort — the reverse rows stay
 * listed, muted and labelled, because they are the same traverse in the other
 * direction (the way back down a climb), which is real information about the
 * activity even if it is not a rankable effort.
 */
const rows = computed<SegmentRow[]>(() =>
  props.segments.flatMap((entry) => {
    const parts = [
      combineMetric(formatDistance(entry.segment.distanceM, props.activityType, props.units)),
    ]
    if (entry.segment.elevationGainM !== null) {
      parts.push(combineMetric(formatElevation(entry.segment.elevationGainM, props.units)))
    }
    const length = parts.join(' · ')

    return entry.efforts.map((effort) => {
      // A segment effort is defined by its distance and elapsed time, so the
      // pace shown here is derived from the average speed rather than a device
      // field the activity may not carry.
      const pace = effort.avgSpeedMps === null ? null : 1 / effort.avgSpeedMps
      return {
        key: `${effort.segmentId}-${effort.direction}-${effort.passNumber}`,
        name: entry.segment.name,
        length,
        time: formatHmsDuration(effort.elapsedSeconds),
        tempo: formatLapTempo(pace, effort.avgSpeedMps, props.activityType, props.units),
        pass:
          effort.passNumber > 1 ? t('activities.segments.pass', { number: effort.passNumber }) : '',
        isReverse: effort.direction === 'reverse',
      }
    })
  }),
)
</script>

<template>
  <div class="overflow-x-auto">
    <table class="w-full min-w-[28rem] border-collapse text-meta">
      <thead>
        <tr class="text-caption border-b border-border text-left">
          <th class="py-2 pe-3 font-medium">{{ t('activities.segments.segment') }}</th>
          <th class="py-2 pe-3 font-medium">{{ t('activities.segments.length') }}</th>
          <th class="py-2 pe-3 font-medium">{{ t('activities.segments.time') }}</th>
          <th class="py-2 font-medium">{{ t('activities.segments.tempo') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="row.key"
          class="border-b border-border/60 last:border-0"
          :class="row.isReverse ? 'text-muted-foreground opacity-60' : ''"
        >
          <td class="py-2 pe-3">
            <span class="font-medium text-foreground">{{ row.name }}</span>
            <span v-if="row.pass" class="text-caption ms-2 text-muted-foreground">
              {{ row.pass }}
            </span>
            <span v-if="row.isReverse" class="text-caption ms-2 text-muted-foreground">
              {{ t('activities.segments.reverse') }}
            </span>
          </td>
          <td class="py-2 pe-3 text-muted-foreground">{{ row.length }}</td>
          <td class="py-2 pe-3 font-medium text-foreground">{{ row.time }}</td>
          <td class="py-2 text-foreground">{{ row.tempo }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
