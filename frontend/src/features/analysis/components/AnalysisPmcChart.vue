<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import type { FitnessPoint, LoadMethod } from '@/features/analysis/types'
import type { ChartRenderOptions } from '@/composables/useChartProvider'

import { themeColor, themeColorAlpha } from '@/lib/themeColor'
import { Card } from '@/components/ui/card'
import { useMetricChart } from '@/composables/useMetricChart'
import { useTheme } from '@/composables/useTheme'
import { isRssBandMethod } from '@/features/analysis/constants'
import { formatMediumDate } from '@/utils/datetime'

const props = defineProps<{
  /** The series points, oldest to newest, as returned by the backend. */
  points: FitnessPoint[]
  /** Which formula the values measure; TRIMP is noted as a different scale. */
  method: LoadMethod
  /** Fatigue time constant the curves were computed with. */
  acuteDays: number
  /** Fitness time constant the curves were computed with. */
  chronicDays: number
}>()

const { t, locale } = useI18n()
const { theme } = useTheme()

// Daily-load bars in a soft mid-gray: muted-foreground sits above the dark card
// and below the light one, so the bars read as background context in either theme
// while the fitness/fatigue/TSB lines stay the layer you actually read. The alpha
// is read per theme so switching theme repaints the canvas.
const loadColor = computed(() =>
  themeColorAlpha('--color-muted-foreground', theme.value === 'dark' ? 0.34 : 0.26),
)
const FITNESS_COLOR = themeColor('--color-brand-mid')
const FATIGUE_COLOR = themeColor('--color-hr')
const FORM_COLOR = themeColor('--color-info')

/** Which caveat the plotted method carries, if any: a different scale, or power-only. */
const methodNote = computed(() => {
  if (props.method === 'trimp') {
    return t('analysis.daily.trimpNote')
  }
  if (props.method.startsWith('rss_')) {
    return isRssBandMethod(props.method)
      ? `${t('analysis.daily.rssNote')} ${t('analysis.daily.bandNote')}`
      : t('analysis.daily.rssNote')
  }
  return ''
})

/** Parameters the visible numbers were derived from, so the chart reads self-labelled. */
const paramsLabel = computed(() =>
  t('analysis.pmc.params', {
    method: t(`analysis.method.${props.method}`),
    acute: props.acuteDays,
    chronic: props.chronicDays,
  }),
)

/**
 * One mixed chart (the performance-management chart): fitness, fatigue and TSB as
 * line overlays over the daily-load bars. Lines come first in the series list so
 * Chart.js paints them on top (the first dataset is the top-most), the curves fill
 * down to zero so a stretch of fatigue reads as a body of work, and the bars sit on
 * the secondary axis: a 170-point day would otherwise squash curves that live near 50.
 */
const chartRender = computed<ChartRenderOptions>(() => {
  const labels = props.points.map((point) => formatMediumDate(point.date, locale.value))
  return {
    kind: 'bar',
    series: [
      {
        label: t('analysis.pmc.seriesFitness'),
        data: props.points.map((point) => Math.round(point.chronic)),
        color: FITNESS_COLOR,
        line: true,
        area: true,
      },
      {
        label: t('analysis.pmc.seriesFatigue'),
        data: props.points.map((point) => Math.round(point.acute)),
        color: FATIGUE_COLOR,
        line: true,
        area: true,
      },
      {
        // TSB crosses zero; filling it would shade both sides of the axis.
        label: t('analysis.pmc.seriesForm'),
        data: props.points.map((point) => Math.round(point.balance)),
        color: FORM_COLOR,
        line: true,
      },
      {
        label: t('analysis.daily.series'),
        data: props.points.map((point) => Math.round(point.load)),
        color: loadColor.value,
        axis: 'secondary',
      },
    ],
    labels,
    valueFormat: (value) => String(Math.round(value)),
    sharedTooltip: true,
  }
})

useMetricChart(chartRender)
</script>

<template>
  <Card class="flex min-w-0 flex-col gap-3">
    <div class="flex flex-wrap items-baseline justify-between gap-2">
      <div class="flex items-center gap-2">
        <span
          class="size-2.5 rounded-full"
          :style="{ backgroundColor: FITNESS_COLOR }"
          aria-hidden="true"
        />
        <h3 class="text-card-heading">{{ t('analysis.pmc.title') }}</h3>
      </div>
      <span class="text-xs text-muted-foreground">{{ paramsLabel }}</span>
    </div>
    <div ref="chartContainer" class="relative h-96 w-full min-w-0 sm:h-[28rem]" />
    <p v-if="methodNote" class="text-body text-muted-foreground">{{ methodNote }}</p>
  </Card>
</template>
