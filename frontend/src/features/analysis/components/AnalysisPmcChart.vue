<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import type { FitnessPoint, LoadMethod } from '@/features/analysis/types'
import type { ChartRenderOptions } from '@/composables/useChartProvider'

import { themeColor } from '@/lib/themeColor'
import { Card } from '@/components/ui/card'
import { useMetricChart } from '@/composables/useMetricChart'
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

const LOAD_COLOR = themeColor('--color-brand')
const FITNESS_COLOR = themeColor('--color-brand-mid')
const FATIGUE_COLOR = themeColor('--color-hr')
const FORM_COLOR = themeColor('--color-info')

/** Parameters the visible numbers were derived from, so the chart reads self-labelled. */
const paramsLabel = computed(() =>
  t('analysis.pmc.params', {
    method: t(`analysis.method.${props.method}`),
    acute: props.acuteDays,
    chronic: props.chronicDays,
  }),
)

/**
 * One mixed chart (the performance-management chart): daily load as bars with
 * fitness, fatigue and TSB as line overlays above them. Lines come first in the
 * series list so Chart.js paints them over the bars.
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
      },
      {
        label: t('analysis.pmc.seriesFatigue'),
        data: props.points.map((point) => Math.round(point.acute)),
        color: FATIGUE_COLOR,
        line: true,
      },
      {
        label: t('analysis.pmc.seriesForm'),
        data: props.points.map((point) => Math.round(point.balance)),
        color: FORM_COLOR,
        line: true,
      },
      {
        label: t('analysis.daily.series'),
        data: props.points.map((point) => Math.round(point.load)),
        color: LOAD_COLOR,
      },
    ],
    labels,
    valueFormat: (value) => String(Math.round(value)),
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
    <div ref="chartContainer" class="relative h-80 w-full min-w-0 sm:h-96" />
    <p v-if="method === 'trimp'" class="text-body text-muted-foreground">
      {{ t('analysis.daily.trimpNote') }}
    </p>
  </Card>
</template>
