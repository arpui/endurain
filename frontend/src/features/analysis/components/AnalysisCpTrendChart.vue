<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import type { CpTrend } from '@/features/analysis/types'
import type { ChartRenderOptions } from '@/composables/useChartProvider'

import { themeColor } from '@/lib/themeColor'
import { Card } from '@/components/ui/card'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { Select } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { useCpTrendQuery } from '@/features/analysis/composables/useAnalysis'
import { useDisplayUnits } from '@/features/activities/composables/useActivityDetail'
import { activityTypeUsesPace } from '@/features/activities/utils/activityType'
import {
  formatPaceClock,
  paceToDisplaySeconds,
  paceUnitLabel,
  speedToDisplay,
  speedUnitLabel,
} from '@/features/activities/utils/format'
import { useMetricChart } from '@/composables/useMetricChart'
import { formatMediumDate } from '@/utils/datetime'

const { t, locale } = useI18n()

const props = defineProps<{
  /**
   * Days of history to fit, mirroring the page's window selector so the trend
   * and the curves above it are read over the same span; 0 means all time.
   */
  windowDays?: number
}>()

/** The sports the chart answers for: only these derive a critical threshold. */
const SPORT_OPTIONS = ['run', 'bike']
/** Selectable moving-average widths, in days each point looks back. */
const MA_OPTIONS = [30, 45, 90]
/** Representative activity type per sport group, for pace-vs-speed formatting. */
const REP_TYPE: Record<string, number> = { run: 1, bike: 4 }

const sport = ref('run')
const maDays = ref(45)

const STRYD_COLOR = themeColor('--color-hr')
const NATIVE_COLOR = themeColor('--color-brand-mid')
const MA_COLOR = themeColor('--color-info')
const THRESHOLD_COLOR = themeColor('--color-muted-foreground')
const ENVELOPE_COLOR = themeColor('--color-goal')

const units = useDisplayUnits()
const trendWindow = computed(() =>
  props.windowDays && props.windowDays > 0 ? props.windowDays : null,
)
const trendQuery = useCpTrendQuery(sport, trendWindow)
const trend = computed<CpTrend | undefined>(() => trendQuery.data.value)

/** An athlete without a single power stream gets the trend in their own speeds. */
const speedMode = computed(() => trend.value?.measure === 'speed')

/** Convert a fitted value (watts, or metres per second in speed mode) into
 *  what this athlete reads: pace for the running sports, speed elsewhere. */
function toDisplay(raw: number): number {
  if (!speedMode.value) return raw
  const rep = REP_TYPE[sport.value] ?? 1
  return activityTypeUsesPace(rep)
    ? paceToDisplaySeconds(1 / raw, rep, units.value)
    : speedToDisplay(raw, rep, units.value)
}

/** Moving average over sessions whose date falls within `maDays` of each point.
 *  Computed here (not on the server) because it is a display choice, and the
 *  selector must repaint without refetching. */
function movingAverage(values: number[]): number[] {
  const points = trend.value?.points ?? []
  const out: number[] = []
  values.forEach((_value, i) => {
    const from = new Date(points[i]?.date ?? '').getTime() - maDays.value * 86_400_000
    let sum = 0
    let count = 0
    for (let j = i; j >= 0; j -= 1) {
      const prev = points[j]
      if (prev === undefined || new Date(prev.date).getTime() < from) break
      sum += values[j] as number
      count += 1
    }
    out.push(count > 0 ? Math.round(sum / count) : 0)
  })
  return out
}

/**
 * The axis floor, derived from what is actually on screen: the fifth percentile
 * of shown values with a small margin, rounded down to a tidy step. Not the
 * absolute minimum: one junk fit from a bad session should not push every real
 * dot into the top third of the plot. A fixed number would do the same harm
 * across athletes (a rower's 150 W against a runner's 400 W), and the y-zoom
 * lets anyone push the floor further by hand.
 */
function dataFloor(series: ChartRenderOptions['series']): number | undefined {
  const values = series.flatMap((s) => s.data).filter((v): v is number => v !== null)
  if (values.length === 0) return undefined
  values.sort((a, b) => a - b)
  const idx = Math.floor((values.length - 1) * 0.05)
  const anchor = values[idx] ?? values[values.length - 1] ?? 0
  const step = anchor >= 300 ? 20 : anchor >= 100 ? 10 : 1
  return Math.max(0, Math.floor((anchor * 0.95) / step) * step)
}

/**
 * The threshold evolution: one dot per session (colored by power source where
 * watts exist) with a moving average through them and the temporal threshold
 * the loads actually used as a dashed line. Dots stay unjoined: sessions are
 * days apart and a connecting line would invent form that was never measured
 * between them. In speed mode the same story is told in pace, and the axis
 * inverts so faster sits higher.
 */
const chartRender = computed<ChartRenderOptions>(() => {
  const points = trend.value?.points ?? []
  const labels = points.map((point) => formatMediumDate(point.date, locale.value))
  const shown = points.map((point) => toDisplay(point.cpW))
  const isStrid = points.map((point) => point.powerSource === 'stryd')
  const envelopeByDate = new Map(
    (trend.value?.envelope ?? []).map((sample) => [sample.date, toDisplay(sample.cpW)]),
  )
  const series: ChartRenderOptions['series'] = [
    {
      label: t('analysis.cpTrend.seriesMovingAverage', { days: maDays.value }),
      data: movingAverage(shown),
      color: MA_COLOR,
      line: true,
    },
  ]
  if (speedMode.value) {
    series.push({
      label: t('analysis.cpTrend.seriesSession'),
      data: shown.map((value) => Math.round(value)),
      color: NATIVE_COLOR,
      dots: true,
    })
  } else {
    series.push(
      {
        label: t('analysis.cpTrend.seriesStryd'),
        data: shown.map((value, i) => (isStrid[i] ? Math.round(value) : null)),
        color: STRYD_COLOR,
        dots: true,
      },
      {
        label: t('analysis.cpTrend.seriesNative'),
        data: shown.map((value, i) => (isStrid[i] ? null : Math.round(value))),
        color: NATIVE_COLOR,
        dots: true,
      },
    )
  }
  if (envelopeByDate.size > 0) {
    series.push({
      label: t('analysis.cpTrend.seriesEnvelope'),
      data: points.map((point) => {
        const value = envelopeByDate.get(point.date)
        return value === undefined ? null : Math.round(value)
      }),
      color: ENVELOPE_COLOR,
      dashed: true,
    })
  }
  const thresholdW = trend.value?.threshold?.valueW
  if (!speedMode.value && thresholdW !== undefined && envelopeByDate.size === 0) {
    // Only in watts and only when no temporal line exists (thin history): with
    // one, it IS the threshold those days used, and a second flat line at the
    // all-time peak just stretches the axis and confuses the read.
    series.push({
      label: t('analysis.cpTrend.seriesThreshold', { watts: Math.round(thresholdW) }),
      data: points.map(() => Math.round(thresholdW)),
      color: THRESHOLD_COLOR,
      dashed: true,
    })
  }
  const rep = REP_TYPE[sport.value] ?? 1
  const showPace = speedMode.value && activityTypeUsesPace(rep)
  return {
    kind: 'line',
    series,
    labels,
    valueFormat: (value) =>
      showPace
        ? `${formatPaceClock(value)}/${paceUnitLabel(rep, units.value)}`
        : speedMode.value
          ? `${value.toFixed(1)} ${speedUnitLabel(rep, units.value)}`
          : `${Math.round(value)} W`,
    invertY: showPace,
    sharedTooltip: true,
    zoom: true,
    zoomY: true,
    yFloor: dataFloor(series),
  }
})

useMetricChart(chartRender)
</script>

<template>
  <Card class="flex min-w-0 flex-col gap-3">
    <div class="flex flex-wrap items-baseline justify-between gap-2">
      <h3 class="text-card-heading">
        {{ speedMode ? t('analysis.cpTrend.titleSpeed') : t('analysis.cpTrend.title') }}
      </h3>
      <div class="flex items-center gap-2">
        <span v-if="trendQuery.isFetching.value" class="text-body text-muted-foreground">
          {{ t('analysis.cpTrend.refreshing') }}
        </span>
        <Select v-model="sport" :aria-label="t('analysis.sport.label')" class="w-36">
          <option v-for="option in SPORT_OPTIONS" :key="option" :value="option">
            {{ t(`home.sports.${option}`) }}
          </option>
        </Select>
        <Select v-model="maDays" :aria-label="t('analysis.cpTrend.maLabel')" class="w-40">
          <option v-for="days in MA_OPTIONS" :key="days" :value="days">
            {{ t('analysis.cpTrend.maOption', { days }) }}
          </option>
        </Select>
      </div>
    </div>
    <ErrorState v-if="trendQuery.isError.value" :title="t('analysis.loadFailed')" />
    <Skeleton v-else-if="trendQuery.isPending.value" class="h-80 w-full" />
    <EmptyState
      v-else-if="(trend?.points.length ?? 0) === 0"
      :title="t('analysis.cpTrend.emptyTitle')"
      :description="t('analysis.cpTrend.emptyDescription')"
    />
    <template v-else>
      <div ref="chartContainer" class="relative h-80 w-full min-w-0 sm:h-96" />
      <p class="text-body text-muted-foreground">{{ t('analysis.cpTrend.note') }}</p>
    </template>
  </Card>
</template>
