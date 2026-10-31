<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import type {
  ActivityIntervals,
  IntervalSegmentKind,
  IntervalSessionShape,
} from '@/features/analysis/types'
import type { Units } from '@/features/activities/utils/format'

import { Badge } from '@/components/ui/badge'
import {
  combineMetric,
  formatDistance,
  formatHmsDuration,
} from '@/features/activities/utils/format'
import { formatLapTempo } from '@/features/activities/utils/metrics'
import { formatClock } from '@/features/analysis/utils/format'

const props = defineProps<{
  /** The breakdown the server computed for this activity. */
  intervals: ActivityIntervals
  /** Numeric activity type; decides pace-vs-speed and distance units. */
  activityType: number
  units: Units
}>()

const { t } = useI18n()

/** Effort kinds, so the summary can count them and the table can colour them. */
const EFFORT_KINDS: IntervalSegmentKind[] = ['interval', 'climb', 'race']

/** One row of the timeline table. */
interface SegmentRow {
  key: string
  kind: IntervalSegmentKind
  start: string
  duration: string
  distance: string
  tempo: string
  power: string
  intensityFactor: string
  grade: string
  contrast: string
  isEffort: boolean
}

const rows = computed<SegmentRow[]>(() =>
  props.intervals.segments
    .filter((segment) => segment.durationS >= 10)
    .map((segment, index) => ({
      key: `${segment.startS}-${index}`,
      kind: segment.kind,
      start: formatClock(segment.startS),
      duration: formatHmsDuration(segment.durationS),
      distance:
        segment.distanceM > 0
          ? combineMetric(formatDistance(segment.distanceM, props.activityType, props.units))
          : '--',
      tempo: formatLapTempo(null, segment.avgSpeedMs, props.activityType, props.units),
      power: segment.avgPowerW > 0 ? `${Math.round(segment.avgPowerW)} W` : '--',
      intensityFactor: segment.intensityFactor > 0 ? segment.intensityFactor.toFixed(2) : '--',
      grade: segment.avgGradePct === null ? '--' : `${segment.avgGradePct.toFixed(1)} %`,
      contrast: segment.contrast > 0 ? `×${segment.contrast.toFixed(2)}` : '--',
      isEffort: EFFORT_KINDS.includes(segment.kind),
    })),
)

/** What the session as a whole read as, and how many efforts it contained. */
const summary = computed(() => {
  const shape = props.intervals.shape as IntervalSessionShape
  const efforts = rows.value.filter((row) => row.isEffort).length
  return {
    label: t(`analysis.intervals.shape.${shape}`),
    efforts,
    effortsLabel:
      efforts === 1 ? t('analysis.intervals.effortsOne') : t('analysis.intervals.effortsMany'),
    threshold: props.intervals.thresholdW === null ? null : Math.round(props.intervals.thresholdW),
    base: Math.round(props.intervals.basePowerW),
  }
})
</script>

<template>
  <div class="flex flex-col gap-3">
    <div class="flex flex-wrap items-center gap-2">
      <Badge variant="secondary">{{ summary.label }}</Badge>
      <span class="text-meta text-muted-foreground">
        {{ summary.efforts }} {{ summary.effortsLabel }}
      </span>
    </div>

    <div class="overflow-x-auto">
      <table class="w-full min-w-[34rem] border-collapse text-meta">
        <thead>
          <tr class="text-caption border-b border-border text-left">
            <th class="py-2 pe-3 font-medium">{{ t('activities.laps.intensity') }}</th>
            <th class="py-2 pe-3 font-medium">{{ t('analysis.intervals.colStart') }}</th>
            <th class="py-2 pe-3 font-medium">{{ t('activities.laps.time') }}</th>
            <th class="py-2 pe-3 font-medium">{{ t('activities.laps.distance') }}</th>
            <th class="py-2 pe-3 font-medium">{{ t('activities.laps.tempo') }}</th>
            <th class="py-2 pe-3 font-medium">{{ t('activities.metrics.avgPower') }}</th>
            <th class="py-2 pe-3 font-medium">IF</th>
            <th class="py-2 pe-3 font-medium">{{ t('analysis.intervals.colContrast') }}</th>
            <th class="py-2 pe-3 font-medium">{{ t('analysis.intervals.colGrade') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.key"
            class="border-b border-border last:border-0"
            :class="row.isEffort ? 'text-foreground' : 'text-muted-foreground'"
          >
            <td class="py-2 pe-3">{{ t(`analysis.intervals.kind.${row.kind}`) }}</td>
            <td class="py-2 pe-3 tabular-nums">{{ row.start }}</td>
            <td class="py-2 pe-3 tabular-nums">{{ row.duration }}</td>
            <td class="py-2 pe-3 tabular-nums">{{ row.distance }}</td>
            <td class="py-2 pe-3 tabular-nums">{{ row.tempo }}</td>
            <td class="py-2 pe-3 tabular-nums">{{ row.power }}</td>
            <td class="py-2 pe-3 tabular-nums">{{ row.intensityFactor }}</td>
            <td class="py-2 pe-3 tabular-nums">{{ row.contrast }}</td>
            <td class="py-2 pe-3 tabular-nums">{{ row.grade }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <p class="text-caption text-muted-foreground">
      {{
        t('analysis.intervals.basis', {
          threshold: summary.threshold === null ? '--' : `${summary.threshold} W`,
          base: `${summary.base} W`,
        })
      }}
    </p>
  </div>
</template>
