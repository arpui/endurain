<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import type { FitnessPoint } from '@/features/analysis/types'
import type { ChartRenderOptions } from '@/composables/useChartProvider'

import { themeColor } from '@/lib/themeColor'
import { Card } from '@/components/ui/card'
import { useMetricChart } from '@/composables/useMetricChart'
import { formatMediumDate } from '@/utils/datetime'

const props = defineProps<{
  /** The series points, oldest to newest, as returned by the backend. */
  points: FitnessPoint[]
}>()

const { t, locale } = useI18n()

const FITNESS_COLOR = themeColor('--color-brand-mid')
const FATIGUE_COLOR = themeColor('--color-hr')
const FORM_COLOR = themeColor('--color-info')

/** Render options: three filled curves sharing the day axis. */
const chartRender = computed<ChartRenderOptions>(() => {
  const labels = props.points.map((point) => formatMediumDate(point.date, locale.value))
  return {
    kind: 'line',
    series: [
      {
        label: t('analysis.pmc.seriesFitness'),
        data: props.points.map((point) => Math.round(point.chronic)),
        color: FITNESS_COLOR,
      },
      {
        label: t('analysis.pmc.seriesFatigue'),
        data: props.points.map((point) => Math.round(point.acute)),
        color: FATIGUE_COLOR,
      },
      {
        label: t('analysis.pmc.seriesForm'),
        data: props.points.map((point) => Math.round(point.balance)),
        color: FORM_COLOR,
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
    <div class="flex items-center gap-2">
      <span
        class="size-2.5 rounded-full"
        :style="{ backgroundColor: FITNESS_COLOR }"
        aria-hidden="true"
      />
      <h3 class="text-card-heading">{{ t('analysis.pmc.title') }}</h3>
    </div>
    <div ref="chartContainer" class="relative h-64 w-full min-w-0" />
  </Card>
</template>
