<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import type { Activity, ActivityLap } from '@/features/activities/types'
import type { Units } from '@/features/activities/utils/format'
import type { LapDynamics } from '@/features/analysis/types'

import {
  activityTypeIsCycling,
  activityTypeIsSwimming,
} from '@/features/activities/utils/activityType'
import {
  combineMetric,
  formatDistance,
  formatElevation,
  formatHmsDuration,
} from '@/features/activities/utils/format'
import { normalizeLaps, summarizeActiveLaps } from '@/features/activities/utils/laps'
import { formatLapTempo } from '@/features/activities/utils/metrics'

const props = defineProps<{
  /** The activity's laps, in recorded order (same source the plain table uses). */
  laps: ActivityLap[]
  /** Dynamics computed by the analysis module, joined by lap id. */
  dynamics: LapDynamics[]
  activity: Activity
  units: Units
}>()

const { t, locale } = useI18n()

const isSwimming = computed(() => activityTypeIsSwimming(props.activity.activityType))
const showElevation = computed(() => !isSwimming.value)
const showStrokeRate = computed(() => isSwimming.value)

/** Ground-per-cycle label follows the sport's own vocabulary. */
const lengthLabelKey = computed(() => {
  if (isSwimming.value) {
    return 'analysis.laps.lengthSwim'
  }
  if (activityTypeIsCycling(props.activity.activityType)) {
    return 'analysis.laps.lengthRide'
  }
  return 'analysis.laps.lengthRun'
})

const normalized = computed(() => normalizeLaps(props.laps, props.activity.activityType))
const hasIntensity = computed(() => normalized.value.some((entry) => entry.lap.intensity !== null))
const hasRest = computed(() => normalized.value.some((entry) => entry.isRest))

const dynamicsById = computed(() => new Map(props.dynamics.map((row) => [row.lapId, row])))

const showCadence = computed(() => props.laps.some((lap) => lap.avgCadence !== null))
const showPower = computed(() => props.dynamics.some((row) => row.avgPowerW !== null))
const showLength = computed(() => props.dynamics.some((row) => row.strideLengthM !== null))
const showRss = computed(() => props.dynamics.some((row) => row.rss !== null))
const showRssMin = computed(() => props.dynamics.some((row) => row.rssPerMin !== null))

const activeSummary = computed(() => {
  if (!hasRest.value) {
    return null
  }
  const summary = summarizeActiveLaps(props.laps, props.activity.activityType)
  if (summary === null) {
    return null
  }
  return {
    distance: combineMetric(
      formatDistance(summary.totalDistance, props.activity.activityType, props.units),
    ),
    time: formatHmsDuration(summary.totalElapsedTime),
    tempo: formatLapTempo(
      summary.avgPace,
      summary.avgSpeed,
      props.activity.activityType,
      props.units,
    ),
  }
})

function decimal(value: number | null, digits: number): string {
  if (value === null) {
    return '--'
  }
  return new Intl.NumberFormat(locale.value, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value)
}

interface LapRow {
  id: number
  index: number
  intensity: string | null
  distance: string
  time: string
  tempo: string
  tempoScore: number | null
  elevation: string
  strokeRate: string
  hr: string
  cadence: string
  power: string
  length: string
  rss: string
  rssMin: string
  isRest: boolean
}

const rows = computed<LapRow[]>(() =>
  normalized.value.map((entry, index) => {
    const lap = entry.lap
    const dynamics = dynamicsById.value.get(lap.id)
    return {
      id: lap.id,
      index: index + 1,
      intensity: lap.intensity,
      distance: combineMetric(
        formatDistance(lap.totalDistance, props.activity.activityType, props.units),
      ),
      time: formatHmsDuration(lap.totalElapsedTime),
      tempo: entry.isRest
        ? '--'
        : formatLapTempo(
            lap.enhancedAvgPace,
            lap.enhancedAvgSpeed,
            props.activity.activityType,
            props.units,
          ),
      tempoScore: entry.isRest ? null : entry.normalizedScore,
      elevation: elevationPair(dynamics, lap),
      strokeRate: lap.avgCadence === null ? '--' : `${Math.round(lap.avgCadence)}`,
      hr: lap.avgHeartRate === null ? '--' : `${Math.round(lap.avgHeartRate)} bpm`,
      cadence: lap.avgCadence === null ? '--' : `${Math.round(lap.avgCadence)}`,
      power:
        dynamics?.avgPowerW === null || dynamics?.avgPowerW === undefined
          ? '--'
          : `${Math.round(dynamics.avgPowerW)} W`,
      length: decimal(dynamics?.strideLengthM ?? null, 2),
      rss: estimated(dynamics, decimal(dynamics?.rss ?? null, 1)),
      rssMin: estimated(dynamics, decimal(dynamics?.rssPerMin ?? null, 2)),
      isRest: entry.isRest,
    }
  }),
)

/** Estimated scores (pace or heart rate, no power meter) carry a marker. */
function estimated(dynamics: LapDynamics | undefined, value: string): string {
  if (value === '--') {
    return value
  }
  return dynamics?.rssSource && dynamics.rssSource !== 'power' ? `\u2248${value}` : value
}

/**
 * Ascent and descent as one honest pair (`+10 / −8 m`), never netted: a rolling
 * lap is not flat, whatever a single signed number would claim. Falls back to the
 * plain ascent when the analysis rows carry no split.
 */
function elevationPair(dynamics: LapDynamics | undefined, lap: ActivityLap): string {
  const gain = dynamics?.elevationGainM ?? null
  const loss = dynamics?.elevationLossM ?? null
  if (gain === null && loss === null) {
    return lap.totalAscent === null
      ? '--'
      : combineMetric(formatElevation(lap.totalAscent, props.units))
  }
  const ascent = formatElevation(gain, props.units)
  const descent = formatElevation(loss, props.units)
  return `+${ascent.value} / \u2212${descent.value} ${ascent.unit}`
}
</script>

<template>
  <div>
    <p v-if="activeSummary" class="text-caption mb-2 text-muted-foreground">
      {{ t('activities.laps.activeSummary') }}:
      <span class="text-foreground">{{ activeSummary.distance }}</span>
      · <span class="text-foreground">{{ activeSummary.time }}</span> ·
      <span class="text-foreground">{{ activeSummary.tempo }}</span>
    </p>
    <div class="overflow-x-auto">
      <table class="w-full min-w-[40rem] border-collapse text-meta">
        <thead>
          <tr class="text-caption border-b border-border text-left">
            <th class="py-2 pe-3 font-medium">{{ t('activities.laps.lap') }}</th>
            <th v-if="hasIntensity" class="py-2 pe-3 font-medium">
              {{ t('activities.laps.intensity') }}
            </th>
            <th class="py-2 pe-3 font-medium">{{ t('activities.laps.distance') }}</th>
            <th class="py-2 pe-3 font-medium">{{ t('activities.laps.time') }}</th>
            <th class="py-2 pe-3 font-medium">{{ t('activities.laps.tempo') }}</th>
            <th v-if="showElevation" class="py-2 pe-3 font-medium">
              {{ t('analysis.laps.desnivel') }}
            </th>
            <th v-if="showStrokeRate" class="py-2 pe-3 font-medium">
              {{ t('activities.laps.strokeRate') }}
            </th>
            <th v-else-if="showCadence" class="py-2 pe-3 font-medium">
              {{ t('analysis.laps.cadence') }}
            </th>
            <th class="py-2 pe-3 font-medium">{{ t('activities.laps.hr') }}</th>
            <th v-if="showPower" class="py-2 pe-3 font-medium">
              {{ t('analysis.laps.power') }}
            </th>
            <th v-if="showLength" class="py-2 pe-3 font-medium">{{ t(lengthLabelKey) }}</th>
            <th v-if="showRss" class="py-2 pe-3 font-medium">
              {{ t('analysis.laps.rss') }}
            </th>
            <th v-if="showRssMin" class="py-2 font-medium">
              {{ t('analysis.laps.rssMin') }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.id"
            class="border-b border-border/60 last:border-0"
            :class="row.isRest ? 'text-muted-foreground opacity-60' : ''"
          >
            <td class="py-2 pe-3 font-medium text-foreground">{{ row.index }}</td>
            <td v-if="hasIntensity" class="py-2 pe-3 capitalize text-muted-foreground">
              {{ row.intensity ?? '--' }}
            </td>
            <td class="py-2 pe-3 text-foreground">{{ row.distance }}</td>
            <td class="py-2 pe-3 text-foreground">{{ row.time }}</td>
            <td class="py-2 pe-3 text-foreground">
              <span>{{ row.tempo }}</span>
              <span
                v-if="row.tempoScore !== null"
                class="mt-1 block h-1 w-16 overflow-hidden rounded-full bg-muted"
                role="progressbar"
                :aria-valuenow="Math.round(row.tempoScore)"
                aria-valuemin="0"
                aria-valuemax="100"
              >
                <span
                  class="block h-full rounded-full bg-brand"
                  :style="{ width: `${row.tempoScore}%` }"
                />
              </span>
            </td>
            <td v-if="showElevation" class="py-2 pe-3 text-muted-foreground">
              {{ row.elevation }}
            </td>
            <td v-if="showStrokeRate" class="py-2 pe-3 text-muted-foreground">
              {{ row.strokeRate }}
            </td>
            <td v-else-if="showCadence" class="py-2 pe-3 text-muted-foreground">
              {{ row.cadence }}
            </td>
            <td class="py-2 pe-3 text-muted-foreground">{{ row.hr }}</td>
            <td v-if="showPower" class="py-2 pe-3 tabular-nums text-foreground">
              {{ row.power }}
            </td>
            <td v-if="showLength" class="py-2 pe-3 tabular-nums text-muted-foreground">
              {{ row.length }}
            </td>
            <td v-if="showRss" class="py-2 pe-3 tabular-nums text-muted-foreground">
              {{ row.rss }}
            </td>
            <td v-if="showRssMin" class="py-2 tabular-nums text-muted-foreground">
              {{ row.rssMin }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-if="showRss" class="text-caption mt-2 text-muted-foreground">
      {{ t('analysis.laps.rssNote') }}
    </p>
  </div>
</template>
