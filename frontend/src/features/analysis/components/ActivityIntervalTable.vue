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
  formatElevation,
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

const { t, locale } = useI18n()

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
  desnivel: string
  rss: string
  rssMin: string
  isEffort: boolean
}

/** One row of the terrain-climb table. */
interface ClimbRow {
  key: string
  range: string
  duration: string
  desnivel: string
  grade: string
  power: string
  intensityFactor: string
  contrast: string
  rssMin: string
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
      // The segments carry mean speed (the same value distance comes from); pace is
      // derived from it so running rows show min/km like the laps table does.
      tempo: formatLapTempo(
        segment.avgSpeedMs && segment.avgSpeedMs > 0 ? 1 / segment.avgSpeedMs : null,
        segment.avgSpeedMs,
        props.activityType,
        props.units,
      ),
      power: segment.avgPowerW > 0 ? `${Math.round(segment.avgPowerW)} W` : '--',
      intensityFactor: segment.intensityFactor > 0 ? segment.intensityFactor.toFixed(2) : '--',
      grade: segment.avgGradePct === null ? '--' : `${segment.avgGradePct.toFixed(1)} %`,
      contrast: segment.contrast > 0 ? `×${segment.contrast.toFixed(2)}` : '--',
      desnivel: elevationPair(segment),
      rss: segment.rss === null ? '--' : segment.rss.toFixed(1),
      rssMin: segment.rssPerMin === null ? '--' : segment.rssPerMin.toFixed(2),
      isEffort: EFFORT_KINDS.includes(segment.kind),
    })),
)

/** Kilometre marks of a climb's stretch: always metric, like the score per km. */
function kmMark(meters: number): string {
  return new Intl.NumberFormat(locale.value, {
    minimumFractionDigits: 1,
    maximumFractionDigits: 2,
  }).format(meters / 1000)
}

const climbRows = computed<ClimbRow[]>(() =>
  props.intervals.climbs.map((climb) => ({
    key: `${climb.startM}`,
    range: `${kmMark(climb.startM)} - ${kmMark(climb.endM)}`,
    duration: formatHmsDuration(climb.durationS),
    desnivel: `+${climb.elevationGainM.toFixed(0)} / \u2212${climb.elevationLossM.toFixed(0)} m`,
    grade: `${climb.avgGradePct.toFixed(1)} %`,
    power: climb.avgPowerW === null ? '--' : `${Math.round(climb.avgPowerW)} W`,
    intensityFactor: climb.intensityFactor === null ? '--' : climb.intensityFactor.toFixed(2),
    contrast: climb.contrast === null ? '--' : `\u00d7${climb.contrast.toFixed(2)}`,
    rssMin: climb.rssPerMin === null ? '--' : climb.rssPerMin.toFixed(2),
  })),
)

/**
 * Ascent and descent of a stretch as one pair (`+10 / −8 m`), never netted; the
 * altitude came from the smoothed track, so GPS jitter does not read as climbing.
 */
function elevationPair(segment: ActivityIntervals['segments'][number]): string {
  if (segment.elevationGainM === null && segment.elevationLossM === null) {
    return '--'
  }
  const ascent = formatElevation(segment.elevationGainM, props.units)
  const descent = formatElevation(segment.elevationLossM, props.units)
  return `+${ascent.value} / \u2212${descent.value} ${ascent.unit}`
}

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

/**
 * The detection rules as one caption, with the real numbers this session used:
 * what a reader needs when a stretch looks mislabelled.
 */
const rulesNote = computed(() => {
  const rules = props.intervals.rules
  if (!rules) {
    return null
  }
  return t('analysis.intervals.rules', {
    effortIf: rules.effortIf.toFixed(2),
    openW: Math.round(rules.openW),
    closeW: Math.round(rules.closeW),
    minS: Math.round(rules.minSegmentS),
    shortS: Math.round(rules.shortSegmentS),
    shortBase: `×${rules.shortBase.toFixed(2)}`,
    strongBase: `×${rules.strongBase.toFixed(2)}`,
    recoveryW: Math.round(rules.recoveryW),
    climbGrade: rules.climbGradePct.toFixed(1),
    climbIf: rules.climbIf.toFixed(2),
  })
})
</script>

<template>
  <div class="flex flex-col gap-3">
    <template v-if="rows.length > 0">
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
              <th class="py-2 pe-3 font-medium">{{ t('analysis.intervals.colDesnivel') }}</th>
              <th class="py-2 pe-3 font-medium">{{ t('analysis.intervals.colRss') }}</th>
              <th class="py-2 font-medium">{{ t('analysis.intervals.colRssMin') }}</th>
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
              <td class="py-2 pe-3 tabular-nums">{{ row.desnivel }}</td>
              <td class="py-2 pe-3 tabular-nums">{{ row.rss }}</td>
              <td class="py-2 tabular-nums">{{ row.rssMin }}</td>
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
      <p v-if="rulesNote" class="text-caption text-muted-foreground">
        {{ rulesNote }}
      </p>
    </template>

    <template v-if="climbRows.length > 0">
      <h3 class="text-card-heading">{{ t('analysis.intervals.tcTitle') }}</h3>
      <div class="overflow-x-auto">
        <table class="w-full min-w-[30rem] border-collapse text-meta">
          <thead>
            <tr class="text-caption border-b border-border text-left">
              <th class="py-2 pe-3 font-medium">{{ t('analysis.intervals.tcColRange') }}</th>
              <th class="py-2 pe-3 font-medium">{{ t('activities.laps.time') }}</th>
              <th class="py-2 pe-3 font-medium">{{ t('analysis.intervals.colDesnivel') }}</th>
              <th class="py-2 pe-3 font-medium">{{ t('analysis.intervals.colGrade') }}</th>
              <th class="py-2 pe-3 font-medium">{{ t('activities.metrics.avgPower') }}</th>
              <th class="py-2 pe-3 font-medium">IF</th>
              <th class="py-2 pe-3 font-medium">{{ t('analysis.intervals.colContrast') }}</th>
              <th class="py-2 font-medium">{{ t('analysis.intervals.colRssMin') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in climbRows"
              :key="row.key"
              class="border-b border-border last:border-0"
            >
              <td class="py-2 pe-3 font-medium tabular-nums text-foreground">{{ row.range }} km</td>
              <td class="py-2 pe-3 tabular-nums">{{ row.duration }}</td>
              <td class="py-2 pe-3 tabular-nums">{{ row.desnivel }}</td>
              <td class="py-2 pe-3 tabular-nums">{{ row.grade }}</td>
              <td class="py-2 pe-3 tabular-nums">{{ row.power }}</td>
              <td class="py-2 pe-3 tabular-nums">{{ row.intensityFactor }}</td>
              <td class="py-2 pe-3 tabular-nums">{{ row.contrast }}</td>
              <td class="py-2 tabular-nums">{{ row.rssMin }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="text-caption text-muted-foreground">
        {{ t('analysis.intervals.tcNote') }}
      </p>
    </template>
  </div>
</template>
